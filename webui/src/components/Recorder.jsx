import { useRef, useState } from "react";
import { uploadRecording } from "../api.js";

// Records from the microphone via MediaRecorder, or accepts an uploaded file.
export default function Recorder({ onUploaded }) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const mediaRef = useRef(null);
  const chunksRef = useRef([]);

  async function start() {
    setError(null);
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mr = new MediaRecorder(stream);
      chunksRef.current = [];
      mr.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mr.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: "audio/webm" });
        stream.getTracks().forEach((t) => t.stop());
        await send(blob, `Recording ${new Date().toLocaleString()}`);
      };
      mr.start();
      mediaRef.current = mr;
      setRecording(true);
    } catch (e) {
      setError("Microphone access denied or unavailable.");
    }
  }

  function stop() {
    mediaRef.current?.stop();
    setRecording(false);
  }

  async function onFile(e) {
    const file = e.target.files?.[0];
    if (file) await send(file, file.name);
    e.target.value = "";
  }

  async function send(blobOrFile, title) {
    setBusy(true);
    setError(null);
    try {
      const rec = await uploadRecording(blobOrFile, title);
      onUploaded?.(rec);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="recorder">
      {!recording ? (
        <button className="btn primary" onClick={start} disabled={busy}>
          🎙️ Record
        </button>
      ) : (
        <button className="btn danger" onClick={stop} disabled={busy}>
          ⏹ Stop &amp; Upload
        </button>
      )}

      <label className="btn ghost">
        📁 Upload file
        <input
          type="file"
          accept="audio/*"
          onChange={onFile}
          disabled={busy}
          hidden
        />
      </label>

      {busy && <span className="hint">Uploading…</span>}
      {error && <span className="error">{error}</span>}
    </div>
  );
}
