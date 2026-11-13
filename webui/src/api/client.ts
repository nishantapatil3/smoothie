import { VITE_API_BASE_URL } from "./config";
import type { SSEEvent } from "../types/api";

const API_BASE = VITE_API_BASE_URL || "/api";

export async function getHealth() {
  const res = await fetch(`${API_BASE}/health`);
  if (!res.ok) throw new Error("Health check failed");
  return res.json();
}

// Settings
export async function getSettings() {
  const res = await fetch(`${API_BASE}/settings`);
  if (!res.ok) throw new Error("Failed to get settings");
  return res.json();
}

export async function saveSettings(updates: Record<string, unknown>) {
  const res = await fetch(`${API_BASE}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  if (!res.ok) throw new Error("Failed to save settings");
  return res.json();
}

// Recordings
export async function listRecordings() {
  try {
    const res = await fetch(`${API_BASE}/recordings`);
    if (!res.ok) throw new Error("Recordings request failed");
    return await res.json();
  } catch {
    throw new Error(
      "Unable to load recordings. Check that the Smoothie backend is running and connected, then try again.",
    );
  }
}

export async function getRecording(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}`);
  if (!res.ok) throw new Error("Failed to get recording");
  return res.json();
}

export async function uploadRecording(file: File, title?: string) {
  const form = new FormData();
  form.append("audio", file);
  if (title) form.append("title", title);
  const res = await fetch(`${API_BASE}/recordings`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error("Upload failed");
  return res.json();
}

export async function deleteRecording(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error("Delete failed");
}

export async function retrySummary(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}/retry-summary`, { method: "POST" });
  if (!res.ok) throw new Error("Retry failed");
  return res.json();
}

export async function regenerateTitle(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}/regenerate-title`, { method: "POST" });
  if (!res.ok) throw new Error("Title regeneration failed");
  return res.json();
}

export async function reprocessRecording(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}/reprocess`, { method: "POST" });
  if (!res.ok) throw new Error("Reprocess failed");
  return res.json();
}

export async function cancelJob(id: string) {
  const res = await fetch(`${API_BASE}/recordings/${id}/job`, { method: "DELETE" });
  if (!res.ok) throw new Error("Cancel failed");
  return res.json();
}

// SSE for real-time progress
export function onRecordingEvents(id: string, onEvent: (event: SSEEvent) => void) {
  const url = `${API_BASE}/recordings/${id}/events`;
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