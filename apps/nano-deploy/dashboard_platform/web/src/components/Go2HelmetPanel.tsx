import { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch } from "../lib";
import { Panel } from "./Panel";

type Person = { track_id: number; state: "helmet"|"no_helmet"|"unknown"; hits: number;
  depth?: { depth_m: number } | null; event_id?: string };
type Status = { active: boolean; loading: boolean; models_ready: boolean; frames: number; error: string|null;
  model_source?: string|null;
  record_count: number; last: { captured_at: number; total: number; counts: Record<string, number>;
    people: Person[]; recording_error?: string|null; inference_ms: number } | null };
type Evidence = { id: string; session_id: string; track_id: number; found_at: number;
  last_seen_at: number; observations: number; confidence: number; depth?: {depth_m:number}|null };
const labels = {helmet:"已戴安全帽",no_helmet:"未戴安全帽",unknown:"待确认"};
const button = "rounded-xl border border-base bg-card px-4 py-2.5 text-sm text-secondary disabled:opacity-50";
function message(error: unknown) {
  const value=String(error).replace(/^Error:\s*/,"");
  try { return String(JSON.parse(value).detail || value); } catch { return value; }
}
async function getBlob(path: string, token: string, signal?: AbortSignal) {
  const r=await fetch(`${API_BASE}${path}`,{headers:{Authorization:`Bearer ${token}`},cache:"no-store",signal});
  if(!r.ok)throw new Error(await r.text());return r.blob();
}
function download(blob: Blob, name: string) {
  const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=name;
  document.body.appendChild(a);a.click();a.remove();window.setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function EvidenceImage({id,token}:{id:string;token:string}) {
  const [src,setSrc]=useState("");
  useEffect(()=>{let closed=false,url="";const c=new AbortController();
    void getBlob(`/api/go2/person/helmet/records/${id}/crop.jpg`,token,c.signal).then(b=>{
      if(!closed){url=URL.createObjectURL(b);setSrc(url);}
    }).catch(()=>{});
    return()=>{closed=true;c.abort();if(url)URL.revokeObjectURL(url);};
  },[id,token]);
  return src?<img src={src} alt="未戴安全帽候选截图" className="h-28 w-20 rounded-lg bg-panel object-contain"/>:
    <div className="h-28 w-20 rounded-lg bg-panel"/>;
}

export function Go2HelmetPanel({token}:{token:string}) {
  const [status,setStatus]=useState<Status|null>(null),[records,setRecords]=useState<Evidence[]>([]);
  const [error,setError]=useState(""),[pending,setPending]=useState(false);
  const [recording,setRecording]=useState(false),[seconds,setSeconds]=useState(0),[hasFrame,setHasFrame]=useState(false);
  const canvas=useRef<HTMLCanvasElement>(null),recorder=useRef<MediaRecorder|null>(null);
  const mounted=useRef(true),freshFrameAt=useRef(0);
  useEffect(()=>{
    mounted.current=true;let busy=false,closed=false,lastFrame=0;const c=new AbortController();
    async function poll() {
      if(busy||closed)return;busy=true;
      try {
        const s=await apiFetch<Status>("/api/go2/person/helmet/status",token,{signal:c.signal});
        if(closed)return;setStatus(s);
        setError("");
        if(s.active&&!s.last) {
          if(canvas.current){const ctx=canvas.current.getContext("2d");if(ctx){ctx.fillStyle="#0f172a";ctx.fillRect(0,0,canvas.current.width,canvas.current.height);}}
          setHasFrame(false);return;
        }
        if(s.active&&s.last?.captured_at===lastFrame)return;
        const blob=await getBlob(s.active?"/api/go2/person/helmet/preview.jpg":"/api/go2/person/camera.jpg",token,c.signal);
        const url=URL.createObjectURL(blob),image=new Image();
        try {
          await new Promise<void>((resolve,reject)=>{image.onload=()=>resolve();image.onerror=()=>reject(new Error("画面解码失败"));image.src=url;});
          if(closed||!canvas.current)return;
          const target=canvas.current;
          // Fixed recording canvas: changing its dimensions would reset captureStream.
          const ctx=target.getContext("2d");if(!ctx)return;
          ctx.fillStyle="#0f172a";ctx.fillRect(0,0,target.width,target.height);
          const scale=Math.min(target.width/image.width,target.height/image.height);
          ctx.drawImage(image,(target.width-image.width*scale)/2,(target.height-image.height*scale)/2,image.width*scale,image.height*scale);
          lastFrame=s.last?.captured_at||0;freshFrameAt.current=Date.now();setHasFrame(true);
        } finally {URL.revokeObjectURL(url);}
      } catch(e){if(!closed){setError(message(e));setHasFrame(false);}}
      finally{busy=false;}
    }
    void poll();const timer=window.setInterval(()=>{if(!document.hidden)void poll();},250);
    return()=>{closed=true;mounted.current=false;c.abort();window.clearInterval(timer);
      if(recorder.current?.state==="recording")recorder.current.stop();};
  },[token]);
  useEffect(()=>{
    let closed=false,busy=false;const c=new AbortController();
    async function refresh(){if(closed||busy)return;busy=true;
      try{const r=await apiFetch<{items:Evidence[]}>("/api/go2/person/helmet/records",token,{signal:c.signal});if(!closed)setRecords(r.items);}
      catch(e){if(!closed)setError(message(e));}finally{busy=false;}}
    void refresh();const timer=window.setInterval(()=>{if(!document.hidden)void refresh();},3000);
    return()=>{closed=true;c.abort();window.clearInterval(timer);};
  },[token]);
  useEffect(()=>{
    if(!recording)return;
    const timer=window.setInterval(()=>{
      setSeconds(s=>s+1);
      if(Date.now()-freshFrameAt.current>3000&&recorder.current?.state==="recording"){
        recorder.current.stop();setError("画面中断，录像已结束并保存。");
      }
    },1000);
    return()=>window.clearInterval(timer);
  },[recording]);
  useEffect(()=>{if(recording&&seconds>=120)recorder.current?.stop();},[recording,seconds]);
  async function command(action:"start"|"stop") {
    setPending(true);setError("");
    try {const s=await apiFetch<Status>(`/api/go2/person/helmet/${action}`,token,{method:"POST"});setStatus(s);}
    catch(e){setError(message(e));}finally{setPending(false);}
  }
  function record() {
    if(recording){recorder.current?.stop();return;}
    if(!canvas.current||!hasFrame||Date.now()-freshFrameAt.current>2000){setError("请等待新鲜画面后再录像。");return;}
    if(typeof MediaRecorder==="undefined"||!canvas.current.captureStream){setError("当前浏览器不支持画面录制，请用 Chrome/Edge 或系统录屏。");return;}
    const mime=["video/webm;codecs=vp8","video/webm","video/mp4"].find(t=>MediaRecorder.isTypeSupported(t));
    if(!mime){setError("浏览器没有可用的录像编码，请使用系统录屏。");return;}
    try {
      const stream=canvas.current.captureStream(10),parts:BlobPart[]=[];
      const r=new MediaRecorder(stream,{mimeType:mime,videoBitsPerSecond:2500000});
      recorder.current=r;
      r.ondataavailable=e=>{if(e.data.size)parts.push(e.data);};
      r.onstop=()=>{
        stream.getTracks().forEach(t=>t.stop());recorder.current=null;
        if(mounted.current)setRecording(false);
        if(parts.length)download(new Blob(parts,{type:mime}),`安全帽多人追踪_${Date.now()}.${mime.includes("mp4")?"mp4":"webm"}`);
      };
      r.onerror=()=>{if(mounted.current)setError("录像失败，请使用系统录屏。");};
      r.start(1000);setSeconds(0);setRecording(true);setError("");
    } catch(e){setError(message(e));}
  }
  async function exportRecords() {
    try {download(await getBlob("/api/go2/person/helmet/export.json",token),"安全帽识别记录.json");}
    catch(e){setError(message(e));}
  }
  const people=status?.last?.people||[],counts=status?.last?.counts;
  return <Panel title="安全帽多人识别" subtitle="自动记录未戴安全帽的人，按编号持续追踪；机器狗保持静止。" className="!p-4 sm:!p-6">
    <div className="mb-4 flex flex-wrap items-center gap-2">
      <button className={button} disabled={pending||status?.active||status?.loading} onClick={()=>void command("start")}>{pending||status?.loading?"准备中…":"开始识别"}</button>
      <button className={button} disabled={pending||!status?.active} onClick={()=>void command("stop")}>停止识别</button>
      <button className={button} disabled={!recording&&(!status?.active||!hasFrame)} onClick={record}>{recording?`结束并下载录像 ${seconds}s`:"录制 Demo"}</button>
      <button className={button} onClick={()=>void exportRecords()}>导出记录</button>
    </div>
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_240px]">
      <div className="relative overflow-hidden rounded-2xl border border-base bg-slate-900">
        <canvas ref={canvas} width={960} height={720} aria-label="安全帽多人识别实时画面" className="block aspect-[4/3] w-full object-contain"/>
        {!hasFrame?<div className="absolute inset-0 flex items-center justify-center px-4 text-center text-sm text-slate-300">{status?.active?"等待新鲜检测画面…":"准备相机画面…"}</div>:null}
        {recording?<span className="absolute right-3 top-3 rounded-full bg-red-600 px-3 py-1 text-xs text-white">● 录像中 {seconds}s</span>:null}
      </div>
      <aside className="rounded-2xl border border-base bg-card p-4">
        <p className="text-sm font-semibold text-primary">当前画面</p>
        <div className="mt-3 grid grid-cols-2 gap-2">{[["总人数",status?.last?.total],["已戴",counts?.helmet],["未戴",counts?.no_helmet],["待确认",counts?.unknown]].map(([label,value])=><div key={String(label)} className="rounded-xl bg-panel p-3"><p className="text-xs text-muted">{label}</p><p className="mt-1 text-xl font-semibold text-primary">{value??"—"}</p></div>)}</div>
        <div className="mt-4 space-y-2">{people.map(p=><div key={p.track_id} className="rounded-lg border border-base px-3 py-2 text-xs"><span className="font-mono">#{p.track_id}</span> <span className={p.state==="no_helmet"?"text-danger":"text-secondary"}>{labels[p.state]}</span>{p.depth?<span className="mt-1 block text-muted">前向约 {p.depth.depth_m.toFixed(2)} 米</span>:null}</div>)}</div>
        <p className="mt-4 text-xs leading-relaxed text-muted">未戴状态连续确认三帧后自动记录。头部遮挡或多人重叠时显示待确认。编号用于画面追踪，长时间离开后可能变化。</p>
        {status?.model_source?<p className="mt-2 text-xs text-muted">模型：{status.model_source==="original_trained_helmet"?"原训练安全帽模型":"开源备用模型"}</p>:null}
      </aside>
    </div>
    {error||status?.error||status?.last?.recording_error?<p role="alert" className="mt-3 text-sm text-danger">{error||status?.error||status?.last?.recording_error}</p>:null}
    <div className="mt-5 border-t border-base pt-4">
      <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-semibold text-primary">自动目标记录</h3><span className="text-xs text-muted">{status?.record_count??records.length} 条 · 按本轮人物编号记录，需复核</span></div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{records.slice(0,12).map(r=><article key={r.id} className="flex gap-3 rounded-xl border border-base p-3">
        <EvidenceImage id={r.id} token={token}/><div className="min-w-0 text-xs text-muted"><p className="font-semibold text-primary">未戴安全帽 · #{r.track_id}</p><p className="mt-2">{new Date(r.found_at*1000).toLocaleString("zh-CN")}</p><p className="mt-1">累计观测 {r.observations} 帧</p><p className="mt-1">检测分数 {(r.confidence*100).toFixed(0)}%</p>
          <button className="mt-2 text-accent-600 underline" onClick={()=>void getBlob(`/api/go2/person/helmet/records/${r.id}/snapshot.jpg`,token).then(b=>download(b,`安全帽记录_${r.id}.jpg`)).catch(e=>setError(message(e)))}>下载证据画面</button></div>
      </article>)}</div>
      {!records.length?<p className="py-5 text-center text-sm text-muted">暂未记录未戴安全帽的人。</p>:null}
    </div>
  </Panel>;
}
