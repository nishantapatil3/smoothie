import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { listRecordings, deleteRecording, retrySummary, onRecordingEvents } from "../api";
import type { RecordingListItem, SSEEvent } from "../types/api";
import { cn, fmtTime } from "../lib/utils";
import Recorder from "./Recorder";

const IN_PROGRESS = new Set(["pending", "transcribing", "summarizing"]);

export default function RecordingsList() {
  const [recordings, setRecordings] = useState<RecordingListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  // One SSE EventSource per in-progress recording, keyed by id so we can tear
  // each down independently when it finishes.
  const subscriptions = useRef<Map<string, () => void>>(new Map());

  const loadRecordings = async () => {
    try {
      setLoading(true);
      const data = await listRecordings();
      setRecordings(data);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to load recordings");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRecordings();
  }, []);

  // Subscribe to live progress for each in-progress recording. Depends only on
  // the *set* of in-progress ids (a stable key), not on `recordings` itself, so
  // a progress tick (which only changes `progress`) does not tear down and
  // re-create the EventSources — that would thrash the connections.
  const inProgressIds = recordings
    .filter((r) => IN_PROGRESS.has(r.status))
    .map((r) => r.id)
    .sort();
  const inProgressKey = inProgressIds.join(",");

  useEffect(() => {
    const subs = subscriptions.current;

    for (const id of inProgressIds) {
      if (subs.has(id)) continue; // already streaming this one
      const cleanup = onRecordingEvents(id, (event: SSEEvent) => {
        setRecordings((prev) =>
          prev.map((p) =>
            p.id === id
              ? {
                  ...p,
                  status: (event.stage ?? p.status) as RecordingListItem["status"],
                  progress: event.progress,
                  generated_title: event.generated_title ?? p.generated_title,
                }
              : p,
          ),
        );
      });
      subs.set(id, cleanup);
    }

    // Unsubscribe recordings that have left the in-progress set (completed,
    // failed, cancelled, or removed).
    for (const [id, cleanup] of subs) {
      if (!inProgressIds.includes(id)) {
        cleanup();
        subs.delete(id);
      }
    }
  }, [inProgressKey]);

  // Close every open EventSource when the component unmounts.
  useEffect(() => {
    const subs = subscriptions.current;
    return () => {
      subs.forEach((cleanup) => cleanup());
      subs.clear();
    };
  }, []);

  async function handleDelete(id: string) {
    if (!confirm("Delete this recording?")) return;
    try {
      await deleteRecording(id);
      loadRecordings();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Delete failed");
    }
  }

  async function handleRetry(id: string) {
    try {
      await retrySummary(id);
      loadRecordings();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Retry failed");
    }
  }

  if (loading && recordings.length === 0) {
    return <div className="flex-1 flex items-center justify-center text-gray-500">Loading…</div>;
  }

  return (
    <div className="flex-1 flex flex-col">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Recordings</h1>
      </div>

      <Recorder onUploaded={loadRecordings} />

      {error && <div className="error mb-4 p-3 bg-red-50 border border-red-200 rounded-lg">{error}</div>}

      {recordings.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-gray-500">
          <p>No recordings yet. Create your first one!</p>
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto space-y-3">
          {recordings.map((r) => (
            <Link
              key={r.id}
              to={`/recordings/${r.id}`}
              className={cn(
                "card box flex items-center gap-4 hover:shadow-md transition-shadow",
                r.status === "failed" && "border-red-200"
              )}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-medium text-gray-900 truncate">{r.generated_title || r.title || "Untitled"}</h3>
                  <span
                    className={cn(
                      "badge",
                      r.status === "completed" && "bg-green-100 text-green-800",
                      r.status === "transcribing" && "bg-blue-100 text-blue-800",
                      r.status === "summarizing" && "bg-yellow-100 text-yellow-800",
                      r.status === "pending" && "bg-gray-100 text-gray-800",
                      r.status === "failed" && "bg-red-100 text-red-800",
                      r.status === "cancelled" && "bg-gray-100 text-gray-800"
                    )}
                  >
                    {r.status}
                  </span>
                </div>
                <p className="text-sm text-gray-500 truncate">
                  {r.duration_sec ? fmtTime(r.duration_sec) : "Processing…"} · {new Date(r.created_at).toLocaleString()}
                </p>
              </div>

              <div className="flex items-center gap-2">
                {r.status === "transcribing" || r.status === "summarizing" ? (
                  <div className="w-32 progress">
                    <div
                      className={cn("progress-bar", r.status === "summarizing" && "indeterminate")}
                      style={{ width: `${Math.round((r.progress || 0) * 100)}%` }}
                    />
                  </div>
                ) : null}
                {r.status === "failed" && (
                  <button
                    onClick={(e) => { e.preventDefault(); handleRetry(r.id); }}
                    className="btn btn-secondary btn-small"
                  >
                    ↻ Retry Summary
                  </button>
                )}
                <button
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(r.id); }}
                  className="btn btn-ghost btn-small text-red-600 hover:text-red-700"
                >
                  🗑
                </button>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

