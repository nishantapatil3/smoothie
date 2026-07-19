// Thin wrappers around the Smoothie backend API.
const base = "/api";

// In-memory tokens; mirrored to localStorage so they survive reloads. The
// Login component sets these via setTokens(); all authenticated calls attach
// the access token, and expired ones are silently refreshed via refresh_token.
let token = localStorage.getItem("smoothie_token") || null;
let refreshToken = localStorage.getItem("smoothie_refresh") || null;
// Guards against firing multiple concurrent refreshes.
let refreshing = null;

export function setTokens({ token: t, refresh_token: r }) {
  token = t || null;
  refreshToken = r || null;
  if (token) localStorage.setItem("smoothie_token", token);
  else localStorage.removeItem("smoothie_token");
  if (refreshToken) localStorage.setItem("smoothie_refresh", refreshToken);
  else localStorage.removeItem("smoothie_refresh");
}

export function getToken() {
  return token;
}

export function clearTokens() {
  setTokens({ token: null, refresh_token: null });
}

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    const err = new Error(detail);
    err.status = res.status;
    throw err;
  }
  // 204 has no body.
  if (res.status === 204) return null;
  return res.json();
}

async function doRefresh() {
  if (!refreshToken) throw new Error("No refresh token.");
  const res = await fetch(`${base}/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: refreshToken }),
  });
  if (!res.ok) throw new Error("Refresh failed.");
  const data = await res.json();
  setTokens(data);
  return data.token;
}

// Run a fetch that needs auth, transparently refreshing + retrying once on 401.
async function authedFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res = await fetch(url, { ...options, headers });
  if (res.status !== 401 || !refreshToken) return res;

  // One refresh attempt; reuse an in-flight refresh if already running.
  try {
    if (!refreshing) refreshing = doRefresh().finally(() => (refreshing = null));
    await refreshing;
  } catch {
    return res; // refresh failed; surface the original 401
  }
  headers.Authorization = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

function authHeaders() {
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export async function getHealth() {
  return handle(await fetch(`${base}/health`));
}

// ── Auth ──────────────────────────────────────────────────────────────
export async function register(username, password) {
  return handle(
    await fetch(`${base}/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
  );
}

export async function login(username, password) {
  return handle(
    await fetch(`${base}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    })
  );
}

export async function getMe() {
  return handle(await authedFetch(`${base}/auth/me`));
}

export async function logout() {
  if (!refreshToken) return;
  try {
    await fetch(`${base}/auth/logout`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        ...authHeaders(),
      },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
  } finally {
    clearTokens();
  }
}

// ── Settings (config.yaml, editable via UI) ───────────────────────────
export async function getSettings() {
  return handle(await authedFetch(`${base}/settings`));
}

export async function saveSettings(updates) {
  return handle(
    await authedFetch(`${base}/settings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(updates),
    })
  );
}

// ── Recordings (require auth) ─────────────────────────────────────────
export async function listRecordings() {
  return handle(await authedFetch(`${base}/recordings`));
}

export async function getRecording(id) {
  return handle(await authedFetch(`${base}/recordings/${id}`));
}

export async function uploadRecording(file, title) {
  const form = new FormData();
  form.append("audio", file);
  if (title) form.append("title", title);
  return handle(
    await authedFetch(`${base}/recordings`, { method: "POST", body: form })
  );
}

export async function deleteRecording(id) {
  const res = await authedFetch(`${base}/recordings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error("Delete failed");
}
