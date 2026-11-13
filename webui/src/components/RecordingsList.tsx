import { useState, useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import {
  listRecordings,
  deleteRecording,
  retrySummary,
  onRecordingEvents,
} from "../api";
import type { RecordingListItem, SSEEvent } from "../types/api";
import { cn, fmtTime } from "../lib/utils";
import Recorder from "./Recorder";
import Icon from "./Icon";

const IN_PROGRESS = new Set(["pending", "transcribing", "summarizing"]);

export default function RecordingsList() {
  const [recordings, setRecordings] = useState<RecordingListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const filtered = recordings.filter(
    (r) =>
      (r.generated_title || r.title || "Untitled")
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (filter === "all" ||
        (filter === "processing"
          ? IN_PROGRESS.has(r.status)
          : r.status === filter)),
  );

  // One SSE EventSource per in-progress recording, keyed by id so we can tear
  // each down independently when it finishes.
  const subscriptions = useRef<Map<string, () => void>>(new Map());

  const loadRecordings = async () => {
    try {
      setLoading(true);
      const data = await listRecordings();
      setRecordings(data);
      setError("");
    } catch (err: unknown) {
      setError(
        err instanceof Error ? err.message : "Failed to load recordings",
      );
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
                  status: (event.stage ??
                    p.status) as RecordingListItem["status"],
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

  return (
    <div className="library-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">CAPTURE. TRANSCRIBE. CLARIFY.</p>
          <h1>
            Your ideas.
            <br />
            <em>Beautifully clear.</em>
          </h1>
          <p className="page-description">
            A little less note-taking. A lot more being present.
          </p>
        </div>
        <span className="heading-icon">
          <Icon name="audio" size={32} />
        </span>
      </div>
      <Recorder onUploaded={loadRecordings} />
      <div className="library-heading">
        <div>
          <h2>
            Your library <span className="count-pill">{recordings.length}</span>
          </h2>
          <p>Every conversation, ready to revisit.</p>
        </div>
        <label className="search-field">
          <Icon name="search" size={18} />
          <input
            aria-label="Search recordings"
            placeholder="Search recordings…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
      </div>
      <div className="library-filters" aria-label="Filter recordings">
        {[
          ["all", "All recordings"],
          ["completed", "Completed"],
          ["processing", "Processing"],
          ["failed", "Failed"],
        ].map(([value, label]) => (
          <button
            key={value}
            className={cn("filter-tab", filter === value && "selected")}
            aria-pressed={filter === value}
            onClick={() => setFilter(value)}
          >
            {label}
          </button>
        ))}
      </div>
      {error && (
        <div
          role="alert"
          className="error mb-4 p-3 bg-red-50 border border-red-200 rounded-lg flex items-center justify-between gap-3"
        >
          {error}
          <button
            className="btn btn-secondary btn-small"
            onClick={loadRecordings}
          >
            Try again
          </button>
        </div>
      )}
      {loading && recordings.length === 0 ? (
        <div className="empty-library" role="status">
          <span className="loading-ring" />
          <h3>Opening your library…</h3>
        </div>
      ) : error && recordings.length === 0 ? null : filtered.length === 0 ? (
        <div className="empty-library">
          <span className="empty-icon">
            <Icon name={recordings.length ? "search" : "audio"} size={30} />
          </span>
          <h3>
            {recordings.length
              ? "No matching recordings"
              : "Good ideas start with a conversation"}
          </h3>
          <p>
            {recordings.length
              ? "Try a different search or filter."
              : "Record a thought or upload audio. We’ll help turn it into something useful."}
          </p>
          {recordings.length > 0 && (
            <button
              className="btn btn-secondary mt-4"
              onClick={() => {
                setSearch("");
                setFilter("all");
              }}
            >
              Clear filters
            </button>
          )}
        </div>
      ) : (
        <div className="recording-list">
          {filtered.map((r) => (
            <Link
              key={r.id}
              to={`/recordings/${r.id}`}
              className={cn(
                "recording-row card box flex items-center gap-4",
                r.status === "failed" && "border-red-200",
              )}
            >
              <span
                className={cn(
                  "recording-art",
                  r.status === "completed" && "ready",
                )}
              >
                <Icon name="audio" size={24} />
              </span>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-medium text-gray-900 truncate">
                    {r.generated_title || r.title || "Untitled"}
                  </h3>
                  <span
                    className={cn(
                      "badge",
                      r.status === "completed" && "bg-green-100 text-green-800",
                      r.status === "transcribing" &&
                        "bg-blue-100 text-blue-800",
                      r.status === "summarizing" &&
                        "bg-yellow-100 text-yellow-800",
                      r.status === "pending" && "bg-gray-100 text-gray-800",
                      r.status === "failed" && "bg-red-100 text-red-800",
                      r.status === "cancelled" && "bg-gray-100 text-gray-800",
                    )}
                  >
                    {r.status}
                  </span>
                </div>
                <p className="text-sm text-gray-500 truncate">
                  {r.duration_sec ? fmtTime(r.duration_sec) : "Processing…"} ·{" "}
                  {new Date(r.created_at).toLocaleString()}
                </p>
              </div>

              <div className="flex items-center gap-2">
                {r.status === "transcribing" || r.status === "summarizing" ? (
                  <div className="w-32 progress">
                    <div
                      className={cn(
                        "progress-bar",
                        r.status === "summarizing" && "indeterminate",
                      )}
                      style={{
                        width: `${Math.round((r.progress || 0) * 100)}%`,
                      }}
                    />
                  </div>
                ) : null}
                {r.status === "failed" && (
                  <button
                    onClick={(e) => {
                      e.preventDefault();
                      handleRetry(r.id);
                    }}
                    className="btn btn-secondary btn-small"
                  >
                    ↻ Retry Summary
                  </button>
                )}
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    handleDelete(r.id);
                  }}
                  aria-label={`Delete ${r.generated_title || r.title || "recording"}`}
                  className="btn btn-ghost btn-small text-red-600 hover:text-red-700"
                >
                  <Icon name="trash" size={17} />
                </button>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
