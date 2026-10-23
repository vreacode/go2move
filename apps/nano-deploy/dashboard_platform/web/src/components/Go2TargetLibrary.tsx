import { useEffect, useState } from "react";
import { API_BASE, apiFetch } from "../lib";

type SavedTarget = { id:string; name:string; kind?:string; description?:string; uploaded_at:number;
  selected?:boolean; discovery_count:number; last_found_at?:number|null };
const button="rounded-lg border border-base px-3 py-1.5 text-xs text-secondary hover:bg-panel disabled:opacity-40";
function failure(error:unknown) {
  const text=String(error).replace(/^Error:\s*/,"");
  try {return String(JSON.parse(text).detail||text);}catch{return text;}
}
function Thumbnail({id,token}:{id:string;token:string}) {
  const [src,setSrc]=useState("");
  useEffect(()=>{let closed=false,url="";const c=new AbortController();
    void fetch(`${API_BASE}/api/go2/person/targets/${id}/image.jpg`,{headers:{Authorization:`Bearer ${token}`},cache:"no-store",signal:c.signal})
      .then(async r=>{if(!r.ok)throw new Error("照片读取失败");return r.blob();})
      .then(b=>{if(!closed){url=URL.createObjectURL(b);setSrc(url);}}).catch(()=>{});
    return()=>{closed=true;c.abort();if(url)URL.revokeObjectURL(url);};
  },[id,token]);
  return src?<img src={src} alt="已保存目标照片" loading="lazy" className="h-24 w-16 shrink-0 rounded-lg bg-panel object-contain"/>:
    <div className="h-24 w-16 shrink-0 rounded-lg bg-panel"/>;
}

export function Go2TargetLibrary({token,currentId,onChanged}:{token:string;currentId?:string;onChanged:()=>Promise<void>}) {
  const [items,setItems]=useState<SavedTarget[]>([]),[error,setError]=useState("");
  const [pending,setPending]=useState(""),[confirm,setConfirm]=useState(""),[query,setQuery]=useState("");
  const [open,setOpen]=useState(true);
  useEffect(()=>{
    if(!open||!token)return;
    let closed=false,busy=false;const c=new AbortController();
    async function refresh(){if(busy||closed)return;busy=true;
      try{const r=await apiFetch<{items:SavedTarget[]}>("/api/go2/person/targets",token,{signal:c.signal});if(!closed){setItems(r.items);setError("");}}
      catch(e){if(!closed)setError(failure(e));}finally{busy=false;}}
    void refresh();const timer=window.setInterval(()=>void refresh(),3000);
    return()=>{closed=true;c.abort();window.clearInterval(timer);};
  },[token,currentId,open]);
  async function action(id:string,kind:"select"|"delete") {
    if(pending)return;setPending(id);setError("");
    const c=new AbortController(),timer=window.setTimeout(()=>c.abort(),45000);
    try{
      await apiFetch(`/api/go2/person/targets/${id}${kind==="select"?"/select":""}`,token,{method:kind==="select"?"POST":"DELETE",signal:c.signal});
      const listing=await apiFetch<{items:SavedTarget[]}>("/api/go2/person/targets",token,{signal:c.signal});
      setItems(listing.items);setConfirm("");await onChanged();
    }catch(e){setError(failure(e));}finally{window.clearTimeout(timer);setPending("");}
  }
  const visible=items.filter(t=>`${t.name} ${t.description||""}`.includes(query.trim()));
  return <details open={open} onToggle={e=>setOpen(e.currentTarget.open)} className="mt-5 border-t border-base pt-4">
    <summary className="cursor-pointer text-sm font-semibold text-primary">目标列表 <span className="ml-2 text-xs font-normal text-muted">{items.length} 个 · 选用后再开始查找</span></summary>
    {open?<div className="mt-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><p className="text-xs text-muted">拍照或上传后自动保存，更换目标不会覆盖旧照片。</p>
        <input aria-label="搜索已保存目标" placeholder="搜索名称" value={query} onChange={e=>setQuery(e.target.value)} className="w-40 rounded-lg border border-base bg-panel px-3 py-1.5 text-xs text-primary"/></div>
      {error?<p role="alert" className="mb-3 text-xs text-danger">{error}</p>:null}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{visible.map(t=><article key={t.id} className="flex gap-3 rounded-xl border border-base bg-card p-3">
        <Thumbnail id={t.id} token={token}/><div className="min-w-0 flex-1 text-xs text-muted">
          <p className="truncate text-sm font-medium text-primary">{t.name}{t.id===currentId?<span className="ml-2 text-xs text-accent-600">当前</span>:null}</p>
          <p className="mt-1">{t.kind==="description"?t.description:"照片目标"}</p>
          <p className="mt-1">{new Date(t.uploaded_at*1000).toLocaleDateString("zh-CN")} · 发现 {t.discovery_count} 次</p>
          {confirm===t.id?<div className="mt-2"><p className="mb-2">{t.id===currentId?"删除当前目标将停止查找。":"删除此目标？"}发现记录会保留。</p><div className="flex gap-2">
            <button className={button+" text-danger"} disabled={!!pending} onClick={()=>void action(t.id,"delete")}>确认删除</button><button className={button} disabled={!!pending} onClick={()=>setConfirm("")}>取消</button></div></div>:
            <div className="mt-2 flex gap-2"><button className={button} disabled={!!pending||t.id===currentId} onClick={()=>void action(t.id,"select")}>{pending===t.id?"处理中…":"选为目标"}</button><button className={button} disabled={!!pending} onClick={()=>setConfirm(t.id)}>删除目标</button></div>}
        </div></article>)}</div>
      {!visible.length?<p className="py-4 text-center text-xs text-muted">{items.length?"没有匹配的目标。":"暂无目标，先拍照或上传一张人物照片。"}</p>:null}
    </div>:null}
  </details>;
}
