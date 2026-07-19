import { useState } from "react";
import { login, register, setTokens } from "../api.js";

// Login / register screen shown until the user has a valid token.
export default function Login({ onAuthed }) {
  const [mode, setMode] = useState("login"); // "login" | "register"
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const action = mode === "register" ? register : login;
      const data = await action(username.trim(), password);
      setTokens(data);
      onAuthed();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  function toggle() {
    setError(null);
    setMode((m) => (m === "login" ? "register" : "login"));
  }

  const isRegister = mode === "register";

  return (
    <div className="auth">
      <form className="auth-card" onSubmit={submit}>
        <h1>🥤 Smoothie</h1>
        <p className="muted">
          {isRegister ? "Create an account" : "Sign in to continue"}
        </p>

        <label>
          Username
          <input
            type="text"
            value={username}
            autoFocus
            onChange={(e) => setUsername(e.target.value)}
            disabled={busy}
          />
        </label>

        <label>
          Password
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </label>

        {error && <div className="error">{error}</div>}

        <button className="btn primary" type="submit" disabled={busy}>
          {busy ? "…" : isRegister ? "Register" : "Sign in"}
        </button>

        <button type="button" className="btn ghost link" onClick={toggle}>
          {isRegister
            ? "Already have an account? Sign in"
            : "Need an account? Register"}
        </button>
      </form>
    </div>
  );
}
