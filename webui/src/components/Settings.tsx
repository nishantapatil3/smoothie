import { useState, useEffect } from "react";
import { getSettings, saveSettings } from "../api";
import type { Settings as SettingsDto, SettingsUpdate } from "../types/api";
import { cn } from "../lib/utils";

export default function Settings() {
  const [settings, setSettings] = useState<SettingsDto | null>(null);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  const loadSettings = async () => {
    try {
      const data = await getSettings();
      setSettings(data);
    } catch (err) {
      console.error(err);
    }
  };

  useEffect(() => {
    loadSettings();
  }, []);

  const handleChange = (key: keyof SettingsDto, value: unknown) => {
    setSettings((s) => (s ? { ...s, [key]: value } : null));
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!settings) return;
    setSaving(true);
    setMessage(null);
    try {
      const updates: Record<string, unknown> = {};
      (Object.keys(settings) as Array<keyof SettingsDto>).forEach((k) => {
        const val = settings[k];
        if (val !== undefined) {
          updates[k] = val;
        }
      });
      await saveSettings(updates as SettingsUpdate);
      setMessage({ type: "success", text: "Settings saved" });
    } catch (err) {
      setMessage({ type: "error", text: err instanceof Error ? err.message : "Failed to save" });
    } finally {
      setSaving(false);
    }
  };

  if (!settings) return <div className="p-4">Loading settings…</div>;

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
        <p className="text-gray-500 mt-1">Changes apply immediately and are saved to config.yaml</p>
      </div>

      {message && (
        <div className={cn("mb-4 p-3 rounded-lg", message.type === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700")}>
          {message.text}
        </div>
      )}

      <form onSubmit={handleSubmit} className="space-y-6">
        <section className="card">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Transcription</h2>
          </div>
          <div className="p-4 space-y-4">
            <div>
              <label className="label">Model</label>
              <select
                value={settings.transcription_model}
                onChange={(e) => handleChange("transcription_model", e.target.value)}
                className="input"
              >
                <option value="tiny">Whisper Tiny (fastest)</option>
                <option value="base">Whisper Base</option>
                <option value="small">Whisper Small</option>
                <option value="medium">Whisper Medium</option>
                {!["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"].includes(settings.transcription_model) && (
                  <option value={settings.transcription_model}>{settings.transcription_model}</option>
                )}
                <option value="large-v3-turbo">Whisper Large v3 Turbo</option>
                <option value="large-v3">Whisper Large v3 (best)</option>
              </select>
            </div>

            <div>
              <label className="label">Processing device</label>
              <select
                value={settings.transcription_device}
                onChange={(e) => handleChange("transcription_device", e.target.value)}
                className="input"
              >
                <option value="auto">Automatic (GPU when available)</option>
                <option value="cpu">CPU</option>
              </select>
            </div>

            <div>
              <label className="label">Language</label>
              <select
                value={settings.transcription_language || ""}
                onChange={(e) => handleChange("transcription_language", e.target.value || null)}
                className="input"
              >
                <option value="">Auto-detect</option>
                <option value="en">English</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
                <option value="de">German</option>
                <option value="it">Italian</option>
                <option value="pt">Portuguese</option>
                <option value="nl">Dutch</option>
                <option value="pl">Polish</option>
                <option value="ja">Japanese</option>
                <option value="zh">Chinese</option>
                <option value="ko">Korean</option>
              </select>
            </div>

            <div className="flex items-center gap-3">
              <input
                type="checkbox"
                id="vad_enabled"
                checked={settings.vad_enabled}
                onChange={(e) => handleChange("vad_enabled", e.target.checked)}
                className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
              />
              <label htmlFor="vad_enabled" className="text-sm font-medium text-gray-700">
                Enable VAD-based chunking (speech-aligned segments)
              </label>
            </div>
          </div>
        </section>

        <section className="card">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Folder imports</h2>
          </div>
          <div className="p-4 space-y-4">
            <label className="flex items-center gap-3 text-sm font-medium text-gray-700">
              <input type="checkbox" checked={settings.watch_enabled}
                onChange={(e) => handleChange("watch_enabled", e.target.checked)}
                className="h-4 w-4 rounded border-gray-300" />
              Automatically import audio from the watch folder
            </label>
            <p className="hint">Originals stay in the watch folder. Smoothie saves audio copies, transcripts, and summaries in the output folder. Folder paths are configured in config.yaml.</p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Scan interval (seconds)</label>
                <input type="number" min="0.1" step="0.1" className="input"
                  value={settings.watch_poll_interval}
                  onChange={(e) => handleChange("watch_poll_interval", parseFloat(e.target.value))} />
              </div>
              <div>
                <label className="label">Wait for unchanged file (seconds)</label>
                <input type="number" min="0" step="1" className="input"
                  value={settings.watch_stable_seconds}
                  onChange={(e) => handleChange("watch_stable_seconds", parseFloat(e.target.value))} />
              </div>
            </div>
          </div>
        </section>

        <section className="card">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Summarization</h2>
          </div>
          <div className="p-4 space-y-4">
            <div>
              <label className="label">Provider</label>
              <select className="input" value={settings.summary_provider}
                onChange={(e) => handleChange("summary_provider", e.target.value)}>
                <option value="api">OpenAI-compatible API</option>
                <option value="codex">ChatGPT subscription via Codex</option>
              </select>
            </div>
            {settings.summary_provider === "codex" ? (
              <>
                <p className="hint">Uses this machine's Codex ChatGPT sign-in and Codex usage allowance. Transcripts are sent to OpenAI for summarization.</p>
                <div>
                  <label className="label">Codex model (optional)</label>
                  <input type="text" className="input" value={settings.summary_codex_model}
                    onChange={(e) => handleChange("summary_codex_model", e.target.value)}
                    placeholder="Use Codex default" />
                </div>
              </>
            ) : (
              <>
            <div>
              <label className="label">LLM Endpoint URL</label>
              <input
                type="url"
                value={settings.summary_url}
                onChange={(e) => handleChange("summary_url", e.target.value)}
                className="input"
                placeholder="http://localhost:11434/v1"
              />
              <p className="hint mt-1">OpenAI-compatible /chat/completions endpoint</p>
            </div>

            <div>
              <label className="label">API Key (optional)</label>
              <input
                type="password"
                value={settings.summary_key}
                onChange={(e) => handleChange("summary_key", e.target.value)}
                className="input"
                placeholder="Leave empty for local endpoints"
              />
            </div>

            <div>
              <label className="label">Model Name</label>
              <input
                type="text"
                value={settings.summary_model}
                onChange={(e) => handleChange("summary_model", e.target.value)}
                className="input"
                placeholder="llama3.2"
              />
            </div>
              </>
            )}
          </div>
        </section>

        <section className="card">
          <div className="p-4 border-b border-gray-200">
            <h2 className="text-lg font-semibold text-gray-900">Server</h2>
          </div>
          <div className="p-4 space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Host</label>
                <input
                  type="text"
                  value={settings.host}
                  onChange={(e) => handleChange("host", e.target.value)}
                  className="input"
                />
              </div>
              <div>
                <label className="label">Port</label>
                <input
                  type="number"
                  value={settings.port}
                  onChange={(e) => handleChange("port", parseInt(e.target.value, 10))}
                  className="input"
                />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="label">Job Poll Interval (seconds)</label>
                <input
                  type="number"
                  step="0.1"
                  value={settings.job_poll_interval}
                  onChange={(e) => handleChange("job_poll_interval", parseFloat(e.target.value))}
                  className="input"
                />
              </div>
            </div>
          </div>
        </section>

        <div className="flex justify-end gap-3 pt-4">
          <button type="button" onClick={loadSettings} className="btn btn-secondary" disabled={saving}>
            Reset
          </button>
          <button type="submit" className="btn btn-primary" disabled={saving}>
            {saving ? "Saving…" : "Save Settings"}
          </button>
        </div>
      </form>
    </div>
  );
}