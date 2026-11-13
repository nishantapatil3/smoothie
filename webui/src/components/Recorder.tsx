import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { uploadRecording } from "../api";
import Icon from "./Icon";
import { cn } from "../lib/utils";

interface RecorderProps {
  onUploaded?: () => void;
}

export default function Recorder({ onUploaded }: RecorderProps) {
  const [recording, setRecording] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(() => setElapsed((n) => n + 1), 1000);
    return () => window.clearInterval(timer);
  }, [recording]);
  useEffect(
    () => () => {
      const media = mediaRef.current;
      if (media && media.state !== "inactive") {
        media.onstop = null;
        media.stop();
      }
      media?.stream.getTracks().forEach((track) => track.stop());
    },
    [],
  );
  const fileRef = useRef<HTMLInputElement | null>(null);
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
      setElapsed(0);
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
    <section
      className={cn("capture-panel", recording && "is-recording")}
      aria-label="Create a recording"
    >
      <div className="capture-copy">
        <span className="eyebrow">A SPACE FOR YOUR NEXT BIG IDEA</span>
        <h2>
          {recording
            ? "You’re in the moment."
            : "Make room for the conversation."}
        </h2>
        <p>
          {recording
            ? "Recording is live. Stop whenever you’re ready to transcribe."
            : "Turn meetings, voice notes, and lightbulb moments into clear transcripts and summaries."}
        </p>
        <div className="capture-actions">
          <button
            className={cn("btn", recording ? "btn-danger" : "btn-primary")}
            onClick={recording ? stop : start}
            disabled={busy}
          >
            <Icon name={recording ? "stop" : "mic"} size={18} />
            {recording ? "Stop & transcribe" : "Start recording"}
          </button>
          <button
            className="btn upload-button"
            onClick={() => fileRef.current?.click()}
            disabled={busy || recording}
          >
            <Icon name="upload" size={17} />
            {busy ? "Uploading…" : "Upload audio"}
          </button>
          <input
            ref={fileRef}
            type="file"
            accept="audio/*"
            onChange={onFile}
            disabled={busy || recording}
            className="hidden"
            aria-label="Upload audio file"
          />
        </div>
        <div className="capture-footnote" role="status">
          {recording ? (
            <>
              <span className="live-dot" /> Recording ·{" "}
              {Math.floor(elapsed / 60)}:{String(elapsed % 60).padStart(2, "0")}
            </>
          ) : busy ? (
            "Uploading your audio…"
          ) : (
            <>
              <Icon name="shield" size={13} /> On-device transcription. Your
              choice of summary provider.
            </>
          )}
        </div>
        {error && (
          <p role="alert" className="error mt-3">
            {error}
          </p>
        )}
      </div>
      <div className="sound-art" aria-hidden="true">
        <div className="sound-orbit orbit-one" />
        <div className="sound-orbit orbit-two" />
        <div className="sound-bars">
          {[
            18, 30, 46, 68, 40, 84, 112, 70, 96, 132, 88, 58, 104, 74, 42, 62,
            34, 20,
          ].map((height, i) => (
            <i key={i} style={{ height, animationDelay: `${i * 0.09}s` }} />
          ))}
        </div>
        <span className="sound-caption">LESS NOISE. MORE MEANING.</span>
      </div>
    </section>
  );
}
