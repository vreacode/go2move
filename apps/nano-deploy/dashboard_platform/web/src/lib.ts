export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export const classNames = (...items: Array<string | false | null | undefined>) =>
  items.filter(Boolean).join(" ");

export const loadToken = () => localStorage.getItem("dashboard_token") ?? "";
export const saveToken = (token: string) => localStorage.setItem("dashboard_token", token);
export const clearToken = () => localStorage.removeItem("dashboard_token");

export async function apiFetch<T>(path: string, token: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `${response.status}`);
  }
  return (await response.json()) as T;
}

export function wsUrl(path: string, token: string) {
  const base = API_BASE || window.location.origin;
  const url = new URL(path, base);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.searchParams.set("token", token);
  return url.toString();
}

export function formatNumber(value?: number | null, suffix = "%") {
  if (value === undefined || value === null || Number.isNaN(value)) return "--";
  return `${value.toFixed(1)}${suffix}`;
}
