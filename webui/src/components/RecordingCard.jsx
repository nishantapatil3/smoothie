const STATUS_LABEL = {
  pending: "Queued",
  transcribing: "Transcribing…",
  summarizing: "Summarizing…",
  completed: "Done",
  failed: "Failed",
};

const IN_PROGRESS = ["pending", "transcribing", "summarizing"];

export default function RecordingCard({ rec, active, onSelect }) {
  const inProgress = IN_PROGRESS.includes(rec.status);
  const pct = Math.round((rec.progress || 0) * 100);
  // Summarizing has no native progress; show an indeterminate bar.
  const indeterminate = rec.status === "summarizing";

  return (
    <button
      className={`card ${active ? "active" : ""}`}
      onClick={() => onSelect(rec.id)}
    >
      <div className="card-title">{rec.title || "Untitled"}</div>
      <div className="card-meta">
        <span className={`badge ${rec.status}`}>
          {STATUS_LABEL[rec.status] || rec.status}
        </span>
        {rec.duration_sec != null && (
          <span className="muted">{rec.duration_sec.toFixed(1)}s</span>
        )}
      </div>
      {inProgress && (
        <div className="progress" title={indeterminate ? "Working…" : `${pct}%`}>
          <div
            className={`progress-bar ${indeterminate ? "indeterminate" : ""}`}
            style={indeterminate ? undefined : { width: `${pct}%` }}
          />
        </div>
      )}
    </button>
  );
}
