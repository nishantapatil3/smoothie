import { useEffect, useState } from "react";
import { getHealth, listRecordings } from "./api.js";
import Recorder from "./components/Recorder.jsx";
import RecordingCard from "./components/RecordingCard.jsx";
import TranscriptView from "./components/TranscriptView.jsx";

export default function App() {
  const [recordings, setRecordings] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [health, setHealth] = useState(null);

  async function refresh() {
    try {
      const list = await listRecordings();
      setRecordings(list);
      // Auto-select the most recent if none selected.
      setSelectedId((cur) => cur ?? list[0]?.id ?? null);
    } catch {
      /* backend may be down */
    }
  }

  useEffect(() => {
    getHealth().then(setHealth).catch(() => setHealth(null));
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);

  function onUploaded(rec) {
    setSelectedId(rec.id);
    refresh();
  }

  return (
    <div className="app">
      <header>
        <h1>🥤 Smoothie</h1>
        <span className="muted">
          local transcription &amp; summaries
          {health && (
            <>
              {" · "}
              <code>{health.transcription_model}</code> ·{" "}
              <code>{health.summary_model}</code>
            </>
          )}
        </span>
      </header>

      <Recorder onUploaded={onUploaded} />

      <div className="layout">
        <aside>
          {recordings.length === 0 ? (
            <p className="muted">No recordings yet. Record or upload one.</p>
          ) : (
            recordings.map((r) => (
              <RecordingCard
                key={r.id}
                rec={r}
                active={r.id === selectedId}
                onSelect={setSelectedId}
              />
            ))
          )}
        </aside>

        <main>
          {selectedId ? (
            <TranscriptView
              id={selectedId}
              onDeleted={() => {
                setSelectedId(null);
                refresh();
              }}
            />
          ) : (
            <div className="detail muted">
              Select a recording to see its transcript and summary.
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
