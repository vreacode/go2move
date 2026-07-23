import { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch, classNames } from "../lib";
import type { ConnectionState, StreamStats } from "../types";

function videoModeLabel(mode: string) {
  if (mode === "visible_raw") return "可见光";
  if (mode === "ir_raw") return "红外";
  return "检测结果";
}

export function VideoStream({
  mode,
  token,
  useFallback,
  onStateChange,
}: {
  mode: "visible_raw" | "ir_raw" | "annotated";
  token: string;
  useFallback: boolean;
  onStateChange?: (state: ConnectionState) => void;
}) {
  const [videoState, setVideoState] = useState<ConnectionState>("idle");
  const [streamStats, setStreamStats] = useState<StreamStats>({});

  useEffect(() => {
    onStateChange?.(videoState);
  }, [videoState, onStateChange]);
  
  const pcRef = useRef<RTCPeerConnection | null>(null);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const statsRef = useRef<{ bytes: number; timestamp: number }>({ bytes: 0, timestamp: 0 });
  const statsTimerRef = useRef<number | null>(null);
  const frameWatchTimerRef = useRef<number | null>(null);
  const reconnectingRef = useRef(false);
  const lastFrameAtRef = useRef(0);

  useEffect(() => {
    if (!token || useFallback) return;
    void connectWebRTC();
    return () => {
      reconnectingRef.current = false;
      if (statsTimerRef.current) {
        window.clearInterval(statsTimerRef.current);
        statsTimerRef.current = null;
      }
      if (frameWatchTimerRef.current) {
        window.clearInterval(frameWatchTimerRef.current);
        frameWatchTimerRef.current = null;
      }
      if (pcRef.current) {
        pcRef.current.close();
        pcRef.current = null;
      }
    };
  }, [token, useFallback, mode]);

  async function connectWebRTC() {
    try {
      setVideoState((prev) => (prev === "idle" ? "connecting" : "reconnecting"));
      setStreamStats({});
      statsRef.current = { bytes: 0, timestamp: 0 };
      lastFrameAtRef.current = 0;
      const pc = new RTCPeerConnection();
      pcRef.current = pc;
      pc.addTransceiver("video", { direction: "recvonly" });

      pc.ontrack = (event) => {
        if (videoRef.current) {
          const stream = event.streams[0] ?? new MediaStream([event.track]);
          videoRef.current.srcObject = stream;
          attachFrameMonitor(videoRef.current);
        }
      };

      pc.onconnectionstatechange = () => {
        const state = pc.connectionState;
        if (state === "connected") setVideoState("connected");
        if (state === "failed") setVideoState("failed");
        if (state === "disconnected") setVideoState("reconnecting");
      };

      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      const answer = await apiFetch<{ sdp: string; type: RTCSdpType }>("/api/stream/webrtc-offer", token, {
        method: "POST",
        body: JSON.stringify({ sdp: offer.sdp, type: offer.type, stream_mode: mode }),
      });
      await pc.setRemoteDescription(answer);

      statsTimerRef.current = window.setInterval(async () => {
        if (!pcRef.current) return;
        const stats = await pcRef.current.getStats();
        stats.forEach((report) => {
          if (report.type === "inbound-rtp" && report.kind === "video") {
            const previous = statsRef.current;
            const bitrate =
              previous.timestamp > 0
                ? ((report.bytesReceived - previous.bytes) * 8) / ((report.timestamp - previous.timestamp) / 1000) / 1000
                : 0;
            statsRef.current = { bytes: report.bytesReceived, timestamp: report.timestamp };
            setStreamStats((prev) => ({
              ...prev,
              fps: report.framesPerSecond ?? prev.fps,
              bitrateKbps: bitrate || prev.bitrateKbps,
            }));
          }
          if (report.type === "track" && report.kind === "video") {
            setStreamStats((prev) => ({
              ...prev,
              width: report.frameWidth ?? prev.width,
              height: report.frameHeight ?? prev.height,
            }));
          }
          if (report.type === "candidate-pair" && report.state === "succeeded") {
            setStreamStats((prev) => ({
              ...prev,
              rttMs: report.currentRoundTripTime ? report.currentRoundTripTime * 1000 : prev.rttMs,
            }));
          }
        });
      }, 1000);

      frameWatchTimerRef.current = window.setInterval(() => {
        const lastFrameAt = lastFrameAtRef.current;
        const frameAgeMs = lastFrameAt ? Date.now() - lastFrameAt : undefined;
        const frozen = frameAgeMs !== undefined && frameAgeMs > 3000;
        setStreamStats((prev) => ({
          ...prev,
          frameAgeMs: frameAgeMs ?? prev.frameAgeMs,
          frozen,
        }));
        if (frozen) {
          setVideoState("reconnecting");
        }
        if (frozen && frameAgeMs && frameAgeMs > 6000 && !reconnectingRef.current && token && !useFallback) {
          reconnectingRef.current = true;
          void reconnectWebRTC();
        }
      }, 1000);
    } catch (err) {
      setVideoState("failed");
    }
  }

  function attachFrameMonitor(video: HTMLVideoElement) {
    const anyVideo = video as HTMLVideoElement & {
      requestVideoFrameCallback?: (callback: (now: number, metadata: { width: number; height: number }) => void) => number;
    };
    if (!anyVideo.requestVideoFrameCallback) return;
    const pump = () => {
      anyVideo.requestVideoFrameCallback?.((_now, metadata) => {
        lastFrameAtRef.current = Date.now();
        reconnectingRef.current = false;
        setVideoState("connected");
        setStreamStats((prev) => ({
          ...prev,
          width: metadata.width || prev.width,
          height: metadata.height || prev.height,
          frameAgeMs: 0,
          frozen: false,
        }));
        pump();
      });
    };
    pump();
  }

  async function reconnectWebRTC() {
    if (statsTimerRef.current) {
      window.clearInterval(statsTimerRef.current);
      statsTimerRef.current = null;
    }
    if (frameWatchTimerRef.current) {
      window.clearInterval(frameWatchTimerRef.current);
      frameWatchTimerRef.current = null;
    }
    if (pcRef.current) {
      pcRef.current.close();
      pcRef.current = null;
    }
    await connectWebRTC();
  }

  return (
    <div className="flex flex-col h-full w-full gap-2">
      <div className="relative overflow-hidden rounded-xl border-2 border-slate-900 bg-black shadow-inner flex-1">
        {!useFallback ? (
          <video ref={videoRef} autoPlay playsInline muted className="h-full w-full object-contain" />
        ) : (
          <img
            alt="fallback preview"
            src={`${API_BASE}/api/stream/fallback?token=${encodeURIComponent(token)}&mode=${encodeURIComponent(mode)}`}
            className="h-full w-full object-contain"
          />
        )}
        <div className="absolute left-3 top-3 flex items-center gap-2 rounded bg-black/60 px-2 py-1 text-[11px] font-mono text-white backdrop-blur">
          <div className={classNames("h-1.5 w-1.5 rounded-full", streamStats.frozen ? "bg-red-500" : "bg-emerald-500 animate-pulse")}></div>
          {streamStats.frozen ? "FROZEN" : `${videoModeLabel(mode).toUpperCase()} / ${videoState.toUpperCase()}`}
        </div>
        <div className="absolute bottom-3 left-3 flex items-center gap-2">
           <button
             onClick={() => {
               if (document.fullscreenElement) {
                 void document.exitFullscreen();
               } else {
                 void videoRef.current?.requestFullscreen();
               }
             }}
             className="rounded border border-white/20 bg-black/40 px-2 py-1 text-[11px] text-white hover:bg-black/60 backdrop-blur"
           >
             全屏
           </button>
        </div>
      </div>
      <div className="grid grid-cols-2 gap-2 text-[10px] sm:grid-cols-4 lg:grid-cols-2 xl:grid-cols-4 2xl:grid-cols-5">
        <div className="rounded border border-base bg-panel px-2 py-1.5 shadow-sm">
          <span className="text-disabled">FPS</span> <span className="float-right font-medium text-secondary">{streamStats.fps ? streamStats.fps.toFixed(1) : "--"}</span>
        </div>
        <div className="rounded border border-base bg-panel px-2 py-1.5 shadow-sm">
          <span className="text-disabled">RES</span> <span className="float-right font-medium text-secondary">{streamStats.width && streamStats.height ? `${streamStats.width}x${streamStats.height}` : "--"}</span>
        </div>
        <div className="rounded border border-base bg-panel px-2 py-1.5 shadow-sm">
          <span className="text-disabled">RTT</span> <span className="float-right font-medium text-secondary">{streamStats.rttMs ? `${streamStats.rttMs.toFixed(0)}ms` : "--"}</span>
        </div>
        <div className="rounded border border-base bg-panel px-2 py-1.5 shadow-sm">
          <span className="text-disabled">AGE</span> <span className="float-right font-medium text-secondary">{streamStats.frameAgeMs !== undefined ? `${streamStats.frameAgeMs.toFixed(0)}ms` : "--"}</span>
        </div>
        <div className="rounded border border-base bg-panel px-2 py-1.5 shadow-sm hidden 2xl:block">
          <span className="text-disabled">KBP</span> <span className="float-right font-medium text-secondary">{streamStats.bitrateKbps ? `${streamStats.bitrateKbps.toFixed(0)}` : "--"}</span>
        </div>
      </div>
    </div>
  );
}
