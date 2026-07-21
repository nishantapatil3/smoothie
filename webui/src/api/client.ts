import { VITE_API_BASE_URL } from "./config";
import type { AdminUser } from "../types/api";

const API_BASE = VITE_API_BASE_URL || "/api";

let accessToken: string | null = null;
let refreshToken: string | null = null;
let refreshing: Promise<string> | null = null;

export function setTokens(tokens: { token: string; refresh_token: string }) {
  accessToken = tokens.token;
  refreshToken = tokens.refresh_token;
  localStorage.setItem("smoothie_token", accessToken);
  localStorage.setItem("smoothie_refresh", refreshToken);
}

export function clearTokens() {
  accessToken = null;
  refreshToken = null;
  localStorage.removeItem("smoothie_token");
  localStorage.removeItem("smoothie_refresh");
}

export function getAccessToken(): string | null {
  if (!accessToken) {
    accessToken = localStorage.getItem("smoothie_token");
  }
  return accessToken;
}

export function getRefreshToken(): string | null {
  if (!refreshToken) {
    refreshToken = localStorage.getItem("smoothie_refresh");
  }
  return refreshToken;
}

async function doRefresh(): Promise<string> {
  const rToken = getRefreshToken();
  if (!rToken) throw new Error("No refresh token");

  const res = await fetch(`${API_BASE}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: rToken }),
  });

  if (!res.ok) throw new Error("Refresh failed");
  const data = await res.json();
  setTokens(data);
  return data.token;
}

async function authedFetch(url: string, options: RequestInit = {}): Promise<Response> {
  const token = getAccessToken();
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let res = await fetch(url, { ...options, headers });
  if (res.status !== 401 || !getRefreshToken()) return res;

  if (!refreshing) {
    refreshing = doRefresh().finally(() => { refreshing = null; });
  }
  try {
    await refreshing;
  } catch {
    return res;
  }

  headers.set("Authorization", `Bearer ${getAccessToken()}`);
  return fetch(url, { ...options, headers });
}

export async function getHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}

// Auth
export async function register(username: string, password: string) {
  const res = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Registration failed");
  const tokens = await res.json();
  setTokens(tokens);
  return tokens;
}

export async function login(username: string, password: string) {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error((await res.json()).detail || "Login failed");
  const tokens = await res.json();
  setTokens(tokens);
  return tokens;
}

export async function getMe() {
  const res = await authedFetch(`${API_BASE}/auth/me`);
  if (!res.ok) throw new Error("Failed to get user");
  return res.json();
}

export async function logout() {
  const rToken = getRefreshToken();
  if (rToken) {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${getAccessToken()}` },
      body: JSON.stringify({ refresh_token: rToken }),
    });
  }
  clearTokens();
}

// Settings
export async function getSettings() {
  const res = await authedFetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error("Failed to get settings");
  return res.json();
}

export async function saveSettings(updates: Record<string, unknown>) {
  const res = await authedFetch(`${API_BASE}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error("Failed to save settings");
  return res.json();
}

// Admin
export async function listUsers(query?: string): Promise<AdminUser[]> {
  const qs = query ? `?q=${encodeURIComponent(query)}` : "";
  const res = await authedFetch(`${API_BASE}/admin/users${qs}`);
  if (!res.ok) throw new Error("Failed to list users");
  return res.json();
}

export async function setUserTier(userId: string, tier: string): Promise<AdminUser> {
  const res = await authedFetch(`${API_BASE}/admin/users/${userId}/tier`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier }),
  });
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || "Failed to set tier");
  return res.json();
}

// Recordings
export async function listRecordings() {
  const res = await authedFetch(`${API_BASE}/recordings`);
  if (!res.ok) throw new Error("Failed to list recordings");
  return res.json();
}

export async function getRecording(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}`);
  if (!res.ok) throw new Error("Failed to get recording");
  return res.json();
}

export async function uploadRecording(file: File, title?: string) {
  const form = new FormData();
  form.append("audio", file);
  if (title) form.append("title", title);
  const res = await authedFetch(`${API_BASE}/recordings`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error("Upload failed");
  return res.json();
}

export async function deleteRecording(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error("Delete failed");
}

export async function retrySummary(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}/retry-summary`, { method: "POST" });
  if (!res.ok) throw new Error("Retry failed");
  return res.json();
}

export async function regenerateTitle(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}/regenerate-title`, { method: "POST" });
  if (!res.ok) throw new Error("Title regeneration failed");
  return res.json();
}

export async function reprocessRecording(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}/reprocess`, { method: "POST" });
  if (!res.ok) throw new Error("Reprocess failed");
  return res.json();
}

export async function cancelJob(id: string) {
  const res = await authedFetch(`${API_BASE}/recordings/${id}/job`, { method: "DELETE" });
  if (!res.ok) throw new Error("Cancel failed");
  return res.json();
}

// SSE for real-time progress
import type { SSEEvent } from "../types/api";

export function onRecordingEvents(id: string, onEvent: (event: SSEEvent) => void) {
  const token = getAccessToken();
  const url = `${API_BASE}/recordings/${id}/events${token ? `?token=${encodeURIComponent(token)}` : ""}`;
  const evtSource = new EventSource(url);

  evtSource.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      onEvent(data);
    } catch (e) {
      console.error("Failed to parse SSE event:", e);
    }
  };

  evtSource.onerror = () => {
    evtSource.close();
  };

  return () => evtSource.close();
}