import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRecording, deleteRecording, retrySummary, onRecordingEvents } from "../api";
import type { RecordingDetail, SSEEvent, Segment } from "../types/api";

const STAGES = ["pending", "transcribing", "summarizing", "completed"] as const;
import { cn, fmtTime } from "../lib/utils";

function TranscriptView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [recording, setRecording] = useState<RecordingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");
  const sseCleanupRef = useRef<(() => void) | null>(null);

  const loadRecording = useCallback(async () => {
    if (!id) return;
    try {
      setLoading(true);
      const data = await getRecording(id);
      setRecording(data);
    } catch {
      setLoading(false);
    }
  }, [id]);

  // Quiet refetch used by live updates: pulls the full recording (including
  // transcript and summary) without flashing the loading state.
  const refreshRecording = useCallback(async () => {
    if (!id) return;
    try {
      const data = await getRecording(id);
      setRecording(data);
    } catch {
      // Keep the last good state; the next SSE tick will retry.
    }
  }, [id]);

  useEffect(() => {
    if (!id) return;
    loadRecording();
  }, [id, loadRecording]);

  const isTerminal =
    recording?.status === "completed" ||
    recording?.status === "failed" ||
    recording?.status === "cancelled";
  // Subscribe once the recording is loaded and still processing; tear down on
  // terminal state. A single boolean avoids re-subscribing on every progress
  // tick (which would thrash the EventSource).
  const subscribed = !!recording && !isTerminal;

  // Tracks the last stage we saw, so we only refetch the full recording on a
  // stage *transition* (transcribing -> summarizing -> completed) rather than
  // on every 0.5s progress tick.
  const lastStageRef = useRef<string | null>(null);

  useEffect(() => {
    if (!id || !subscribed) return;
    lastStageRef.current = recording?.status ?? null;

    const cleanup = onRecordingEvents(id, (event: SSEEvent) => {
      setRecording((prev) =>
        prev
          ? {
              ...prev,
              // The backend streams `stage`; the UI renders from `status`,
              // so map it through.
              status: (event.stage ?? prev.status) as typeof prev.status,
              progress: event.progress,
              error: event.error,
            }
          : null,
      );

      // When the stage advances, the transcript and/or summary just became
      // available in the DB — refetch so they render live instead of waiting
      // for a manual page refresh.
      if (event.stage && event.stage !== lastStageRef.current) {
        lastStageRef.current = event.stage;
        refreshRecording();
      }
    });
    sseCleanupRef.current = cleanup;

    return () => {
      if (cleanup) cleanup();
    };
  }, [id, subscribed, refreshRecording]);

  useEffect(() => {
    return () => {
      if (sseCleanupRef.current) sseCleanupRef.current();
    };
  }, []);

  if (loading && !recording) return <div className="detail">Loading…</div>;
  if (!recording) return <div className="detail">Not found.</div>;
  const rec = recording;

  async function handleDelete() {
    if (!confirm(`Delete "${rec.title || "this recording"}"?`)) return;
    await deleteRecording(rec.id);
    navigate("/recordings");
  }

  async function handleRetrySummary() {
    setRetrying(true);
    setRetryError("");
    try {
      await retrySummary(rec.id);
    } catch (err: unknown) {
      setRetryError(err instanceof Error ? err.message : "Retry failed");
    } finally {
      setRetrying(false);
    }
  }

  return (
    <div className="detail">
      <div className="detail-head">
        <h2>{recording.title || "Untitled"}</h2>
        <button className="btn btn-ghost btn-small" onClick={handleDelete}>
          🗑 Delete
        </button>
      </div>

      {recording.status === "failed" && (
        <div className="error box">
          <div>Failed: {recording.error}</div>
          {recording.full_text ? (
            <button
              className="btn btn-primary btn-small mt-2"
              onClick={handleRetrySummary}
              disabled={retrying}
            >
              {retrying ? "Retrying…" : "↻ Retry summary"}
            </button>
          ) : (
            <div className="muted small mt-2">
              Transcript not ready — re-run the full job to retry.
            </div>
          )}
          {retryError && <div className="muted small mt-2">{retryError}</div>}
        </div>
      )}

      {recording.status === "cancelled" && (
        <div className="error box">
          <div>Job was cancelled: {recording.error}</div>
          <button className="btn btn-primary btn-small mt-2" onClick={() => window.location.reload()}>
            ↻ Reload to re-process
          </button>
        </div>
      )}

      {["pending", "transcribing", "summarizing", "completed"].includes(recording.status) && (
        <div className="workflow box">
          {(["pending", "transcribing", "summarizing", "completed"] as const).map((stage) => {
            const idx = STAGES.indexOf(stage);
            const currentIdx = STAGES.indexOf(recording.status as (typeof STAGES)[number]);
            const isCompleted = recording.status === "completed";
            // The final "completed" node becomes a green checkmark once the
            // whole pipeline finishes, rather than lingering as the active step.
            const done = idx < currentIdx || (stage === "completed" && isCompleted);
            const active = idx === currentIdx && !isCompleted;
            return (
              <div key={stage} className="flex items-center">
                <span
                  className={cn(
                    "workflow-dot",
                    done && "done",
                    active && "active",
                  )}
                >
                  {done ? "✓" : idx + 1}
                </span>
                <span
                  className={cn(
                    "workflow-label",
                    done && "text-green-700",
                    active && "text-gray-900 font-medium",
                    !done && !active && "text-gray-400",
                  )}
                >
                  {stage === "transcribing"
                    ? `Transcribing ${Math.round((recording.progress || 0) * 100)}%`
                    : stage.charAt(0).toUpperCase() + stage.slice(1)}
                </span>
                {stage !== "completed" && (
                  <span className={cn("workflow-line", done && "done")} />
                )}
              </div>
            );
          })}
        </div>
      )}

      {recording.summary && (
        <section className="card mt-4">
          <div className="box">
            <h3 className="text-lg font-semibold mb-2">Summary</h3>
            <div className="markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{recording.summary}</ReactMarkdown>
            </div>
            {recording.summary_provider && (
              <div className="muted small mt-2">via {recording.summary_provider}</div>
            )}
          </div>
        </section>
      )}

      <section className="mt-4">
        <h3 className="text-lg font-semibold mb-2">Transcript</h3>
        {recording.full_text ? (
          <div className="transcript card box">
            {recording.segments?.length ? (
              recording.segments.map((s: Segment, i: number) => (
                <p key={i} className="mb-2">
                  <span className="ts text-xs text-gray-500 mr-2">
                    {fmtTime(s.start)}–{fmtTime(s.end)}
                  </span>
                  {s.text}
                </p>
              ))
            ) : (
              <p className="whitespace-pre-wrap">{recording.full_text}</p>
            )}
          </div>
        ) : recording.status === "failed" ? (
          /limit/i.test(recording.error || "") ? (
            <div className="error box mt-2">
              Transcription limit reached. Free tier allows 30 minutes — upgrade to Standard or Pro to continue.
            </div>
          ) : /too short/i.test(recording.error || "") ? (
            <div className="error box mt-2">
              Audio is too short to transcribe. Record at least a second of speech and try again.
            </div>
          ) : (
            <p className="muted mt-2">Transcription failed{recording.error ? `: ${recording.error}` : "."}</p>
          )
        ) : (
          <p className="muted mt-2">Waiting for transcription…</p>
        )}
      </section>
    </div>
  );
}

export default TranscriptView;