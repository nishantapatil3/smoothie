import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRecording, deleteRecording, retrySummary, regenerateTitle, reprocessRecording, onRecordingEvents } from "../api";
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
  const [retitling, setRetitling] = useState(false);
  const [retitleError, setRetitleError] = useState("");
  const [rerunning, setRerunning] = useState(false);
  const [rerunError, setRerunError] = useState("");
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
              generated_title: event.generated_title ?? prev.generated_title,
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

  // Determine the workflow steps
  let workflowSteps = ["pending", "transcribing", "summarizing"];
  if (rec.summary) {
    workflowSteps = [...workflowSteps, "regenerate title", "completed"];
  } else {
    workflowSteps = [...workflowSteps, "completed"];
  }

  // Determine the current index in the workflowSteps array
  let currentIndex;
  if (rec.status === "completed") {
    currentIndex = workflowSteps.length - 1;
  } else {
    const statusIndexMap = {
      pending: 0,
      transcribing: 1,
      summarizing: 2
    };
    currentIndex = statusIndexMap[rec.status];
  }

  return (

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

  async function handleRegenerateTitle() {
    setRetitling(true);
    setRetitleError("");
    try {
      const data = await regenerateTitle(rec.id);
      setRecording((prev) => (prev ? { ...prev, generated_title: data.generated_title } : prev));
    } catch (err: unknown) {
      setRetitleError(err instanceof Error ? err.message : "Title regeneration failed");
      // Refresh so a 409 (no summary) surfaces the real state.
      loadRecording();
    } finally {
      setRetitling(false);
    }
  }

  async function handleReprocess() {
    setRerunning(true);
    setRerunError("");
    try {
      await reprocessRecording(rec.id);
      // Optimistic UI: move to pending immediately.
      setRecording((prev) => (prev ? { ...prev, status: "pending", progress: 0.0, error: null } : prev));
    } catch (err: unknown) {
      setRerunError(err instanceof Error ? err.message : "Reprocess failed");
      // Refresh so a 404/409 surfaces the real state.
      loadRecording();
    } finally {
      setRerunning(false);
    }
  }

  return (
    <div className="detail">
      <div className="detail-head">
        <h2>{recording.generated_title || recording.title || "Untitled"}</h2>
        <button className="btn btn-ghost btn-small" onClick={handleDelete}>
          🗑 Delete
        </button>
      </div>

      {recording.summary && (
        <section className="card mt-4">
          <div className="box">
            <div className="flex items-center justify-between mb-2">
              <h3 className="text-lg font-semibold">Summary</h3>
              <button
                className="btn btn-secondary btn-small"
                onClick={handleRegenerateTitle}
                disabled={retitling}
                title="Regenerate the short title from this summary"
              >
                {retitling ? "Regenerating…" : "↻ Regenerate title"}
              </button>
            </div>
            <div className="markdown">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{recording.summary}</ReactMarkdown>
            </div>
            {recording.summary_provider && (
              <div className="muted small mt-2">via {recording.summary_provider}</div>
            )}
            {retitleError && <div className="muted small mt-2 text-red-600">{retitleError}</div>}
          </div>
        </section>
      )}

      {["pending", "transcribing", "summarizing", "completed"].includes(recording.status) && (
        <div className="workflow box">
          {workflowSteps.map((step, index) => {
            const isCompleted = recording.status === "completed";
            const done = index < currentIndex || (step === "completed" && isCompleted);
            const active = index === currentIndex && !isCompleted;
            return (
              <div key={step} className="flex items-center justify-between w-full">
                <div className="flex items-center gap-4">
                  <span
                    className={cn(
                      "workflow-dot",
                      done && "done",
                      active && "active",
                    )}
                  >
                    {done ? "✓" : index + 1}
                  </span>
                  <span
                    className={cn(
                      "workflow-label",
                      done && "text-green-700",
                      active && "text-gray-900 font-medium",
                      !done && !active && "text-gray-400",
                    )}
                  >
                    {step === "transcribing"
                      ? `Transcribing ${Math.round((recording.progress || 0) * 100)}%`
                      : step.charAt(0).toUpperCase() + step.slice(1)}
                  </span>
                  {step !== "completed" && (
                    <span className={cn("workflow-line", done && "done")} />
                  )}
                </div>
                <button
                  className="btn btn-ghost"
                  onClick={async () => {
                    setRerunning(true);
                    setRerunError("");
                    try {
                      if (step === "pending" || step === "transcribing" || step === "completed") {
                        await reprocessRecording(rec.id); // full rerun
                      } else if (step === "summarizing") {
                        await retrySummary(rec.id); // resume summarization
                      } else if (step === "regenerate title") {
                        await regenerateTitle(rec.id); // regenerate title
                      }
                    } catch (err: unknown) {
                      setRerunError(err instanceof Error ? err.message : "Rerun failed");
                    } finally {
                      setRerunning(false);
                    }
                  }}
                  disabled={rerunning}
                  title={`Rerun from ${step}`}
                >
                  {rerunning ? "Rerunning…" : `Re-run ${step}`}
                </button>
              </div>
            );
          })}
        </div>
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