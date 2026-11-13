import { useState, useEffect, useRef, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRecording, deleteRecording, retrySummary, regenerateTitle, reprocessRecording, onRecordingEvents } from "../api";
import type { RecordingDetail, SSEEvent, Segment, JobStage } from "../types/api";

import { cn, fmtTime } from "../lib/utils";

function TranscriptView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [recording, setRecording] = useState<RecordingDetail | null>(null);
  const [loading, setLoading] = useState(true);
  // Which workflow step is currently re-running (null when idle). A per-step
  // marker — rather than a single boolean — so only the clicked step shows the
  // spinner and the others don't look like they're all re-running too.
  const [rerunningStep, setRerunningStep] = useState<string | null>(null);
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

  // Determine the workflow steps - always show all steps in the pipeline
  const workflowSteps = ["pending", "transcribing", "summarizing", "regenerate title", "completed"];

  // Determine the current index in the workflowSteps array. Only the in-flight
  // stages map to a step; failed/cancelled fall through to 0 (the workflow box
  // isn't rendered for those statuses anyway).
  const statusIndexMap: Partial<Record<JobStage, number>> = {
    pending: 0,
    transcribing: 1,
    summarizing: 2,
  };
  const currentIndex =
    rec.status === "completed" ? workflowSteps.length - 1 : statusIndexMap[rec.status] ?? 0;

  async function handleDelete() {
    if (!confirm(`Delete "${rec.title || "this recording"}"?`)) return;
    await deleteRecording(rec.id);
    navigate("/recordings");
  }

  // Per-step re-run. Each entry re-runs ONLY its own step and declares the
  // prerequisite that must already be satisfied — so a re-run can never quietly
  // kick off the steps before it. `ready: false` disables the button with a
  // hint instead of firing a request the backend would reject with a 409.
  //
  //   transcribing      -> full reprocess. It's the first real step, and a
  //                        fresh transcript necessarily invalidates the summary
  //                        and title, so re-running it re-runs everything after.
  //   summarizing       -> summary (+ title) only; reuses the existing
  //                        transcript. Needs a transcript.
  //   regenerate title  -> title only, from the existing summary. Needs a
  //                        summary. Does NOT touch transcription or summary.
  const rerunConfig: Record<
    string,
    { run: () => Promise<void>; ready: boolean; blockedHint: string; note: string }
  > = {
    transcribing: {
      run: async () => {
        await reprocessRecording(rec.id);
        setRecording((prev) => (prev ? { ...prev, status: "pending", progress: 0.0, error: null } : prev));
      },
      ready: true,
      blockedHint: "",
      note: "Re-transcribe (also re-summarizes and re-titles)",
    },
    summarizing: {
      run: async () => {
        await retrySummary(rec.id);
        setRecording((prev) => (prev ? { ...prev, status: "summarizing", error: null } : prev));
      },
      ready: !!rec.full_text,
      blockedHint: "Transcribe first",
      note: "Re-summarize from the existing transcript",
    },
    "regenerate title": {
      run: async () => {
        const data = await regenerateTitle(rec.id);
        setRecording((prev) => (prev ? { ...prev, generated_title: data.generated_title } : prev));
      },
      ready: !!rec.summary,
      blockedHint: "Summarize first",
      note: "Regenerate the title from the existing summary",
    },
  };

  return (
    <div className="detail">
      <div className="detail-head">
        <h2>{recording.generated_title || recording.title || "Untitled"}</h2>
        <button className="btn btn-ghost btn-small" onClick={handleDelete}>
          🗑 Delete
        </button>
      </div>

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
                {rerunConfig[step] && (
                  <button
                    className="btn btn-ghost"
                    onClick={async () => {
                      setRerunningStep(step);
                      setRerunError("");
                      try {
                        await rerunConfig[step].run();
                      } catch (err: unknown) {
                        setRerunError(err instanceof Error ? err.message : "Rerun failed");
                        // Refresh so a 404/409 (missing prerequisite) surfaces
                        // the real state instead of a stale optimistic one.
                        loadRecording();
                      } finally {
                        setRerunningStep(null);
                      }
                    }}
                    // Disabled while any step is re-running (no concurrent runs)
                    // or when this step's prerequisite isn't met yet.
                    disabled={rerunningStep !== null || !rerunConfig[step].ready}
                    title={rerunConfig[step].ready ? rerunConfig[step].note : rerunConfig[step].blockedHint}
                  >
                    {rerunningStep === step ? "Rerunning…" : "↻ Re-run"}
                  </button>
                )}
              </div>
            );
          })}
          {rerunError && <div className="muted small mt-2 text-red-600">{rerunError}</div>}
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
          /too short/i.test(recording.error || "") ? (
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