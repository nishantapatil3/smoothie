import { useEffect, useState } from "react";
import { getSettings, saveSettings } from "../api.js";

// Editable config sections. `type` drives rendering; `hint` is helper text.
// Fields whose value may be null (language, tune_*) use text/number inputs that
// map an empty string to null on save.
const SECTIONS = [
  {
    title: "Transcription",
    fields: [
      { key: "transcription_model", label: "Model", type: "text",
        hint: "Applies on the next transcription (model reloads lazily)." },
      { key: "transcription_language", label: "Language", type: "text-nullable",
        hint: "Empty = auto-detect (e.g. en, es, fr)." },
      { key: "vad_enabled", label: "VAD chunking", type: "bool" },
      { key: "vad_threshold", label: "VAD threshold", type: "number", step: 0.01 },
      { key: "vad_min_speech_ms", label: "VAD min speech (ms)", type: "number" },
      { key: "vad_min_silence_ms", label: "VAD min silence (ms)", type: "number" },
      { key: "vad_speech_pad_ms", label: "VAD speech pad (ms)", type: "number" },
      { key: "vad_chunk_target_sec", label: "VAD chunk target (s)", type: "number" },
      { key: "vad_chunk_max_sec", label: "VAD chunk max (s)", type: "number" },
      { key: "tune_enabled", label: "Whisper tuning", type: "bool" },
      { key: "tune_beam_size", label: "beam_size", type: "number-nullable" },
      { key: "tune_condition_on_previous_text", label: "condition_on_previous_text",
        type: "bool-nullable" },
      { key: "tune_no_speech_threshold", label: "no_speech_threshold",
        type: "number-nullable", step: 0.01 },
      { key: "tune_hallucination_silence_threshold", label: "hallucination_silence_threshold",
        type: "number-nullable", step: 0.01 },
      { key: "trim_enabled", label: "Trim silence", type: "bool" },
      { key: "trim_threshold", label: "Trim threshold (dB)", type: "number" },
      { key: "trim_min_silence", label: "Trim min silence (s)", type: "number", step: 0.1 },
    ],
  },
  {
    title: "Summary",
    fields: [
      { key: "summary_url", label: "Endpoint URL", type: "text" },
      { key: "summary_key", label: "API key", type: "password",
        hint: "Stored in config.yaml; leave blank for keyless endpoints." },
      { key: "summary_model", label: "Model", type: "text" },
    ],
  },
  {
    title: "Auth",
    fields: [
      { key: "auth_access_ttl_minutes", label: "Access token TTL (min)", type: "number" },
      { key: "auth_refresh_ttl_days", label: "Refresh token TTL (days)", type: "number" },
      { key: "auth_min_password_len", label: "Min password length", type: "number" },
      { key: "job_poll_interval", label: "Worker poll interval (s)", type: "number", step: 0.1 },
    ],
  },
];

// Convert a raw form string to the value type the backend expects.
function coerce(type, raw) {
  if (type === "number" || type === "number-nullable") {
    if (raw === "" || raw === null) return type === "number-nullable" ? null : raw;
    const n = Number(raw);
    return Number.isNaN(n) ? raw : n;
  }
  if (type === "bool" || type === "bool-nullable") {
    if (raw === "" || raw === null) return type === "bool-nullable" ? null : raw;
    return raw === true || raw === "true";
  }
  // text / text-nullable
  return raw === "" && type === "text-nullable" ? null : raw;
}

function Field({ field, value, onChange }) {
  const isNullable =
    field.type.includes("nullable") || field.type === "text-nullable";
  if (field.type === "bool" || field.type === "bool-nullable") {
    return (
      <label className="field">
        <span>{field.label}</span>
        <select
          value={value === null || value === "" ? "" : value ? "true" : "false"}
          onChange={(e) =>
            onChange(e.target.value === "" ? null : e.target.value === "true")
          }
        >
          {isNullable && <option value="">default</option>}
          <option value="true">on</option>
          <option value="false">off</option>
        </select>
      </label>
    );
  }
  const inputType =
    field.type === "password"
      ? "password"
      : field.type === "number" || field.type === "number-nullable"
      ? "number"
      : "text";
  return (
    <label className="field">
      <span>{field.label}</span>
      <input
        type={inputType}
        step={field.step}
        value={value ?? ""}
        placeholder={field.type === "text-nullable" ? "auto" : ""}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}

export default function Settings() {
  const [form, setForm] = useState(null);
  const [status, setStatus] = useState(null); // {type, msg}
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getSettings()
      .then(setForm)
      .catch(() => setStatus({ type: "error", msg: "Failed to load settings." }));
  }, []);

  function setField(key, raw) {
    const field = SECTIONS.flatMap((s) => s.fields).find((f) => f.key === key);
    setForm((f) => ({ ...f, [key]: coerce(field.type, raw) }));
  }

  async function onSave() {
    setBusy(true);
    setStatus(null);
    try {
      const updated = await saveSettings(form);
      setForm(updated);
      setStatus({ type: "ok", msg: "Saved to config.yaml." });
    } catch (e) {
      setStatus({ type: "error", msg: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (!form) {
    return <div className="detail muted">Loading settings…</div>;
  }

  return (
    <div className="settings">
      <h2>⚙ Settings</h2>
      <p className="muted">
        Changes apply immediately and are written to <code>config.yaml</code>.
      </p>

      {SECTIONS.map((section) => (
        <div className="card settings-section" key={section.title}>
          <h3>{section.title}</h3>
          <div className="settings-grid">
            {section.fields.map((field) => (
              <div key={field.key}>
                <Field
                  field={field}
                  value={form[field.key]}
                  onChange={(raw) => setField(field.key, raw)}
                />
                {field.hint && <div className="field-hint">{field.hint}</div>}
              </div>
            ))}
          </div>
        </div>
      ))}

      {status && (
        <div className={`status ${status.type === "ok" ? "ok" : "error"}`}>
          {status.msg}
        </div>
      )}

      <button className="btn primary" onClick={onSave} disabled={busy}>
        {busy ? "Saving…" : "Save settings"}
      </button>
    </div>
  );
}
