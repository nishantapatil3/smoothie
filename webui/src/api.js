// Thin wrappers around the Smoothie backend API.
const base = "/api";

async function handle(res) {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return res.json();
}

export async function getHealth() {
  return handle(await fetch(`${base}/health`));
}

export async function listRecordings() {
  return handle(await fetch(`${base}/recordings`));
}

export async function getRecording(id) {
  return handle(await fetch(`${base}/recordings/${id}`));
}

export async function uploadRecording(file, title) {
  const form = new FormData();
  form.append("audio", file);
  if (title) form.append("title", title);
  return handle(
    await fetch(`${base}/recordings`, { method: "POST", body: form })
  );
}

export async function deleteRecording(id) {
  const res = await fetch(`${base}/recordings/${id}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) throw new Error("Delete failed");
}
