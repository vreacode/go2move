import { CpuChipIcon, MapIcon, RadioIcon, WrenchScrewdriverIcon } from "@heroicons/react/24/outline";
import { classNames } from "../lib";
import type { SystemStatus } from "../types";

export type SidebarTab = "live" | "navigation" | "perception" | "diagnostics";

const items = [
  { id: "live" as const, code: "01", label: "实时监控", hint: "视频与操控", icon: RadioIcon },
  { id: "navigation" as const, code: "02", label: "巡航航线", hint: "地图与导航", icon: MapIcon },
  { id: "perception" as const, code: "03", label: "智能感知", hint: "媒体与模型", icon: CpuChipIcon },
  { id: "diagnostics" as const, code: "04", label: "系统诊断", hint: "资源与底盘", icon: WrenchScrewdriverIcon },
];

function signalFor(id: SidebarTab, status: SystemStatus | null | undefined) {
  if (id === "live") return status?.agent_online && status?.board_online ? "ONLINE" : "OFFLINE";
  if (id === "navigation") return status?.navigation_running ? "RUNNING" : status?.mapping_running ? "MAPPING" : "READY";
  if (id === "perception") return status?.model_status?.running ? "MODEL ON" : "STANDBY";
  const baseReady = !!status?.base_status?.wheeltec_robot_online && !!status?.base_status?.cmd_vel_subscriber_online && !!status?.base_status?.odom_online;
  return baseReady ? "READY" : "CHECK";
}

export function SidebarMenu({ activeTab, onChange, status }: { activeTab: SidebarTab; onChange: (tab: SidebarTab) => void; status?: SystemStatus | null }) {
  return (
    <aside
      className="flex min-h-screen w-60 shrink-0 flex-col border-r border-sky-200/70 bg-[linear-gradient(180deg,rgba(255,255,255,.98),rgba(241,248,255,.92))] p-4 shadow-[8px_0_28px_rgba(47,112,180,.08)] backdrop-blur"
      aria-label="工作区导航"
    >
      <div className="mb-8 border-b border-sky-100 pb-5">
        <div className="flex items-center justify-between gap-3">
          <p className="text-[10px] font-bold uppercase tracking-[0.22em] text-sky-700">CARMOVE / UGV</p>
          <span className="font-mono text-[10px] text-slate-400">FIELD 01</span>
        </div>
        <p className="mt-3 text-lg font-bold tracking-tight text-slate-800">操作工作区</p>
        <p className="mt-1 text-xs leading-5 text-slate-500">按页面组织现场控制、导航和系统状态。</p>
      </div>
      <nav className="space-y-2" aria-label="功能页面">
        {items.map(({ id, code, label, hint, icon: Icon }) => {
          const active = activeTab === id;
          const signal = signalFor(id, status);
          const signalGood = signal === "ONLINE" || signal === "READY" || signal === "RUNNING" || signal === "MODEL ON";
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              aria-current={active ? "page" : undefined}
              className={classNames(
                "group relative flex w-full items-center gap-3 rounded-xl border px-3 py-3 text-left transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-sky-500/40",
                active
                  ? "border-sky-400 bg-gradient-to-r from-sky-100 to-blue-50 text-sky-700 shadow-[0_8px_20px_rgba(37,99,235,.16)]"
                  : "border-transparent text-slate-500 hover:border-sky-200 hover:bg-white hover:text-sky-700 hover:shadow-sm",
              )}
            >
              <span
                className={classNames(
                  "absolute inset-y-2 left-0 w-1 rounded-r",
                  active ? "bg-gradient-to-b from-cyan-400 to-blue-600 shadow-[0_0_12px_rgba(37,99,235,.65)]" : "bg-transparent",
                )}
              />
              <span
                className={classNames(
                  "grid h-8 w-8 place-items-center rounded-md border",
                  active ? "border-sky-300 bg-white/80 text-sky-600" : "border-slate-200 bg-slate-50 text-slate-500",
                )}
              >
                <Icon className="h-5 w-5" aria-hidden="true" />
              </span>
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2 text-sm font-semibold">
                  <span className="font-mono text-[10px] text-slate-400">{code}</span>
                  {label}
                </span>
                <span className={classNames("mt-0.5 block text-[11px] font-normal", active ? "text-sky-700/80" : "text-slate-400 group-hover:text-slate-500")}>{hint}</span>
              </span>
              <span className={classNames("hidden font-mono text-[9px] tracking-tight lg:block", signalGood ? "text-sky-600" : "text-slate-400")}>{signal}</span>
            </button>
          );
        })}
      </nav>
      <div className="mt-auto border-t border-sky-100 pt-4 text-[10px] leading-5 text-slate-400">
        <p className="font-mono uppercase tracking-[0.16em] text-sky-700/70">Navigation mode</p>
        <p className="mt-1">点击菜单切换工作区 · 当前页可通过地址栏分享</p>
      </div>
    </aside>
  );
}
