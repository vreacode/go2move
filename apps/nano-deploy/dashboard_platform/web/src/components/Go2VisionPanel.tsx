import {useState} from "react";
import {Go2PersonPanel} from "./Go2PersonPanel";
import {Go2HelmetPanel} from "./Go2HelmetPanel";
import {classNames} from "../lib";

export function Go2VisionPanel({token}:{token:string}) {
  const [mode,setMode]=useState<"helmet"|"person">("helmet");
  return <div className="space-y-4">
    <div className="flex gap-2 rounded-xl border border-base bg-panel p-2" role="tablist" aria-label="视觉功能">
      {([["helmet","安全帽多人识别"],["person","照片 / 描述找人"]] as const).map(([key,label])=><button key={key} role="tab" aria-selected={mode===key} onClick={()=>setMode(key)}
        className={classNames("flex-1 rounded-lg px-3 py-3 text-sm font-medium",mode===key?"bg-accent-500/10 text-accent-600":"text-muted")}>{label}</button>)}
    </div>
    {mode==="helmet"?<Go2HelmetPanel token={token}/>:<Go2PersonPanel token={token}/>}
  </div>;
}
