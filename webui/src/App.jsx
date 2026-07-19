import { useEffect, useState } from "react";
import { getHealth, getMe, listRecordings, getToken, setTokens, clearTokens, logout } from "./api.js";
import Login from "./components/Login.jsx";
import Settings from "./components/Settings.jsx";
import Recorder from "./components/Recorder.jsx";
import RecordingCard from "./components/RecordingCard.jsx";
import TranscriptView from "./components/TranscriptView.jsx";

export default function App() {
  const [authed, setAuthed] = useState(() => Boolean(getToken()));
  const [view, setView] = useState("app"); // "app" | "settings"
  const [username, setUsername] = useState(null);
  const [recordings, setRecordings] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [health, setHealth] = useState(null);

  // Validate any stored token on first load.
  useEffect(() => {
    if (!authed) return;
    getMe()
      .then((me) => setUsername(me.username))
      .catch(() => {
        // Token invalid/expired and no usable refresh — drop back to login.
        clearTokens();
        setUsername(null);
        setRecordings([]);
        setSelectedId(null);
        setAuthed(false);
      });
  }, []);

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
    if (!authed) return;
    getHealth().then(setHealth).catch(() => setHealth(null));
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [authed]);

  function onUploaded(rec) {
    setSelectedId(rec.id);
    refresh();
  }

  function handleLogout() {
    logout(); // revokes the refresh token server-side + clears local tokens
    setUsername(null);
    setRecordings([]);
    setSelectedId(null);
    setAuthed(false);
  }

  function onAuthed() {
    getMe()
      .then((me) => setUsername(me.username))
      .catch(() => {});
    setAuthed(true);
  }

  if (!authed) {
    return <Login onAuthed={onAuthed} />;
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
        <span className="header-right">
          <button
            className={`btn ghost ${view === "app" ? "active" : ""}`}
            onClick={() => setView("app")}
          >
            🏠 Home
          </button>
          <button
            className={`btn ghost ${view === "settings" ? "active" : ""}`}
            onClick={() => setView("settings")}
          >
            ⚙ Settings
          </button>
          {username && <span className="muted">{username}</span>}
          <button className="btn ghost" onClick={handleLogout}>
            Log out
          </button>
        </span>
      </header>

      {view === "settings" ? (
        <Settings />
      ) : (
        <>
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
        </>
      )}
    </div>
  );
}
