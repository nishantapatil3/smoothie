import { useState, useEffect } from "react";
import { listUsers, setUserTier } from "../api";
import type { AdminUser } from "../types/api";
import { cn } from "../lib/utils";

const TIERS = ["free", "standard", "pro"] as const;

export default function Subscriptions({ onTierChanged }: { onTierChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<AdminUser[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ type: "success" | "error"; text: string } | null>(null);

  // Server-side search: the query is sent to GET /api/admin/users?q= so the
  // full user table never reaches the browser. Debounced, and stale responses
  // are dropped so results always reflect the latest query.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      setSearchError(null);
      setSearching(false);
      return;
    }
    let cancelled = false;
    setSearching(true);
    const t = setTimeout(async () => {
      try {
        const users = await listUsers(q);
        if (!cancelled) {
          setResults(users);
          setSearchError(null);
        }
      } catch (err) {
        if (!cancelled) setSearchError(err instanceof Error ? err.message : "Search failed");
      } finally {
        if (!cancelled) setSearching(false);
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [query]);

  const handleTierChange = async (userId: string, tier: string) => {
    setSavingId(userId);
    setMessage(null);
    // Optimistic: reflect the new tier immediately, roll back on failure.
    const prev = results;
    setResults((us) => us.map((u) => (u.id === userId ? { ...u, tier } : u)));
    try {
      const updated = await setUserTier(userId, tier);
      setResults((us) => us.map((u) => (u.id === userId ? updated : u)));
      setMessage({ type: "success", text: `Updated ${updated.username} to ${tier}` });
      onTierChanged();
    } catch (err) {
      setResults(prev);
      setMessage({ type: "error", text: err instanceof Error ? err.message : "Failed to update tier" });
    } finally {
      setSavingId(null);
    }
  };

  const q = query.trim();

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Subscriptions</h1>
        <p className="text-gray-500 mt-1">Search for a user and change their subscription tier.</p>
      </div>

      {message && (
        <div
          className={cn(
            "mb-4 p-3 rounded-lg",
            message.type === "success" ? "bg-green-50 text-green-700" : "bg-red-50 text-red-700",
          )}
        >
          {message.text}
        </div>
      )}

      <section className="card">
        <div className="p-4">
          <label className="label" htmlFor="user-search">
            Search users
          </label>
          <input
            id="user-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="input"
            placeholder="Type a username…"
            autoComplete="off"
          />

          {searchError && <div className="mt-3 p-3 rounded-lg bg-red-50 text-red-700 text-sm">{searchError}</div>}

          {q && !searchError && (
            <div className="mt-4 divide-y divide-gray-100">
              {searching ? (
                <p className="text-sm text-gray-500 py-3">Searching…</p>
              ) : results.length === 0 ? (
                <p className="text-sm text-gray-500 py-3">No users match "{query}".</p>
              ) : (
                results.map((u) => (
                  <div key={u.id} className="flex items-center justify-between gap-4 py-3">
                    <div className="min-w-0">
                      <div className="font-medium text-gray-900 truncate">{u.username}</div>
                      <div className="text-xs text-gray-500">{u.usage_minutes}m transcribed</div>
                    </div>
                    <select
                      value={u.tier}
                      onChange={(e) => handleTierChange(u.id, e.target.value)}
                      disabled={savingId === u.id}
                      className="input w-auto"
                    >
                      {TIERS.map((t) => (
                        <option key={t} value={t}>
                          {t.charAt(0).toUpperCase() + t.slice(1)}
                        </option>
                      ))}
                    </select>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
