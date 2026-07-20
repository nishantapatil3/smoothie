import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadRecording } from "../api";
import { cn } from "../lib/utils";

interface RecorderProps {
  onUploaded?: () => void;
}

export default function Recorder({ onUploaded }: RecorderProps) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const navigate = useNavigate();
  const mediaRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);

  async function start() {
    setError("");
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
    } catch {
      setError("Microphone access denied or unavailable.");
    }
  }

  function stop() {
    mediaRef.current?.stop();
    setRecording(false);
  }

  async function onFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) await send(file, file.name);
    e.target.value = "";
  }

  async function send(blobOrFile: Blob | File, title: string) {
    setBusy(true);
    setError("");
    try {
      await uploadRecording(blobOrFile as File, title);
      // Prefer a caller-supplied callback (e.g. refresh the list in place);
      // fall back to navigating to the recordings list when none is given.
      if (onUploaded) {
        onUploaded();
      } else {
        navigate("/recordings");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="recorder flex flex-wrap items-center gap-2 mb-4">
      {!recording ? (
        <button className={cn("btn btn-danger btn-small", busy && "opacity-70")} onClick={start} disabled={busy}>
          🎙️ Record
        </button>
      ) : (
        <button className={cn("btn btn-danger btn-small", busy && "opacity-70")} onClick={stop} disabled={busy}>
          ⏹ Stop & Upload
        </button>
      )}

      <label className={cn("btn btn-small bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-500", busy && "opacity-70")}>
        📁 Upload file
        <input
          type="file"
          accept="audio/*"
          onChange={onFile}
          disabled={busy}
          className="hidden"
        />
      </label>

      {busy && <span className="hint">Uploading…</span>}
      {error && <span className="error">{error}</span>}
    </div>
  );
}