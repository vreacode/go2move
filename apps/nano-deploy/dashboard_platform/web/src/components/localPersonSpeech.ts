export type LocalDiscoveryStatus = {
  active: boolean;
  target: { id: string; name: string } | null;
  latest_event?: { id: string; target_id: string; target_name: string; found_at: number; last_seen_at: number; source: string } | null;
  last?: { captured_at: number; suspected_target: boolean } | null;
};

// Session-only: reloading or enabling never replays saved discoveries.
export class LocalPersonSpeech {
  private enabled = false;
  private initialized = false;
  private targetId: string | null = null;
  private armedAt = Infinity;
  private seen = new Set<string>();
  constructor(private cancel: () => void) {}

  private remember(status: LocalDiscoveryStatus | null) {
    if (status?.latest_event?.id) this.seen.add(status.latest_event.id);
  }
  enable(value: boolean, status: LocalDiscoveryStatus | null, now: number) {
    this.cancel(); this.enabled = value; this.armedAt = now;
    this.initialized = !!status; this.targetId = status?.target?.id ?? null;
    this.remember(status);
  }
  quiet(status: LocalDiscoveryStatus | null, now: number) {
    this.cancel(); this.armedAt = now; this.remember(status);
  }
  next(status: LocalDiscoveryStatus, now: number): string | null {
    const targetId = status.target?.id ?? null;
    if (!this.initialized) {
      this.initialized = true; this.targetId = targetId; this.remember(status); return null;
    }
    if (targetId !== this.targetId || !status.active) {
      this.cancel(); this.targetId = targetId; this.armedAt = now; this.remember(status); return null;
    }
    const event = status.latest_event;
    if (!this.enabled) { this.remember(status); return null; }
    if (!event || this.seen.has(event.id)) return null;
    // Invalid/old events are consumed so later history refreshes cannot replay them.
    this.seen.add(event.id);
    if (event.source !== "live_camera" || event.target_id !== targetId || !status.last?.suspected_target
      || !Number.isFinite(event.found_at) || event.found_at < this.armedAt
      || !Number.isFinite(event.last_seen_at) || now - event.last_seen_at < -1 || now - event.last_seen_at > 3
      || !Number.isFinite(status.last.captured_at) || now - status.last.captured_at < -1 || now - status.last.captured_at > 3) return null;
    return `发现疑似${event.target_name || status.target?.name || "目标人物"}，请查看画面并现场确认。`;
  }
  dispose() { this.cancel(); }
}
