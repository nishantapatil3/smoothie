// Renders a recording's transcript + summary, polling while in progress.
import { useEffect, useState } from "react";
import { getRecording, deleteRecording } from "../api.js";

const TERMINAL = ["completed", "failed"];
const IN_PROGRESS = ["pending", "transcribing", "summarizing"];

function ProgressBar({ status, progress }) {
  const pct = Math.round((progress || 0) * 100);
  const indeterminate = status === "summarizing";
  const label =
    status === "transcribing"
      ? `Transcribing… ${pct}%`
      : status === "summarizing"
        ? "Summarizing…"
        : "Queued…";
  return (
    <div className="progress box">
      <div className="progress-label">{label}</div>
      <div className="progress" title={indeterminate ? "Working…" : `${pct}%`}>
        <div
          className={`progress-bar ${indeterminate ? "indeterminate" : ""}`}
          style={indeterminate ? undefined : { width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default function TranscriptView({ id, onDeleted }) {
  const [rec, setRec] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    let timer;

    async function load() {
      try {
        const data = await getRecording(id);
        if (cancelled) return;
        setRec(data);
        setLoading(false);
        if (!TERMINAL.includes(data.status)) {
          timer = setTimeout(load, 2500);
        }
      } catch {
        if (!cancelled) setLoading(false);
      }
    }

    setLoading(true);
    load();
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [id]);

  if (loading && !rec) return <div className="detail">Loading…</div>;
  if (!rec) return <div className="detail">Not found.</div>;

  async function onDelete() {
    if (!confirm(`Delete "${rec.title || "this recording"}"?`)) return;
    await deleteRecording(rec.id);
    onDeleted?.();
  }

  return (
    <div className="detail">
      <div className="detail-head">
        <h2>{rec.title || "Untitled"}</h2>
        <button className="btn ghost small" onClick={onDelete}>
          🗑 Delete
        </button>
      </div>

      {rec.status === "failed" && (
        <div className="error box">Failed: {rec.error}</div>
      )}

      {IN_PROGRESS.includes(rec.status) && (
        <ProgressBar status={rec.status} progress={rec.progress} />
      )}

      {rec.summary && (
        <section>
          <h3>Summary</h3>
          <div className="markdown">{rec.summary}</div>
          {rec.summary_provider && (
            <div className="muted small">via {rec.summary_provider}</div>
          )}
        </section>
      )}

      <section>
        <h3>Transcript</h3>
        {rec.full_text ? (
          <div className="transcript">
            {rec.segments?.length ? (
              rec.segments.map((s, i) => (
                <p key={i}>
                  <span className="ts">
                    {fmt(s.start)}–{fmt(s.end)}
                  </span>{" "}
                  {s.text}
                </p>
              ))
            ) : (
              <p>{rec.full_text}</p>
            )}
          </div>
        ) : (
          <p className="muted">Waiting for transcription…</p>
        )}
      </section>
    </div>
  );
}

function fmt(sec) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}:${String(s).padStart(2, "0")}`;
}
