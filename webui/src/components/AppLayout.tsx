import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../api";
import { Outlet, Link, useLocation } from "react-router-dom";
import { VITE_WEBUI_VERSION } from "../api/config";
import { cn } from "../lib/utils";
import Icon from "./Icon";

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const health = useQuery({
    queryKey: ["backend-health"],
    queryFn: () => getHealth(AbortSignal.timeout(4000)),
    refetchInterval: 10000,
    refetchOnWindowFocus: true,
    retry: false,
  });
  const connection = health.isPending ? "checking" : health.isError || health.data?.status !== "ok" ? "disconnected" : "connected";
  const connectionLabel = connection === "checking" ? "Checking backend…" : connection === "connected" ? "Backend connected" : "Backend disconnected";
  const location = useLocation();
  const isSettings = location.pathname.startsWith("/settings");
  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link">
        Skip to content
      </a>
      <aside className={cn("sidebar", sidebarOpen && "is-open")}>
        <Link to="/" className="brand" onClick={() => setSidebarOpen(false)}>
          <span className="brand-mark">
            <Icon name="audio" size={25} />
          </span>
          smoothie<span className="brand-period">.</span>
        </Link>
        <div className="nav-label">SMOOTHIE</div>
        <nav aria-label="Main navigation">
          <Link
            to="/recordings"
            onClick={() => setSidebarOpen(false)}
            aria-current={!isSettings ? "page" : undefined}
            className={cn("nav-item", !isSettings && "active")}
          >
            <Icon name="grid" />
            Recordings
            <Icon name="arrow" size={16} />
          </Link>
          <Link
            to="/settings"
            onClick={() => setSidebarOpen(false)}
            aria-current={isSettings ? "page" : undefined}
            className={cn("nav-item", isSettings && "active")}
          >
            <Icon name="settings" />
            Settings
          </Link>
        </nav>
        <div className="sidebar-bottom">
          <div className="privacy-note">
            <Icon name="shield" />
            <strong>Your audio. Your space.</strong>
            <p>
              Transcribe on your device.
              <br />
              Choose where summaries run.
            </p>
          </div>
          <div className="sidebar-version">
            <Icon name="audio" size={13} /> Local-first audio{" "}
            <span>v{VITE_WEBUI_VERSION}</span>
          </div>
        </div>
      </aside>
      <div className="app-content">
        <header className="topbar">
          <div className="flex items-center gap-3">
            <button
              className="mobile-menu btn btn-ghost"
              aria-label="Toggle navigation"
              aria-expanded={sidebarOpen}
              onClick={() => setSidebarOpen(!sidebarOpen)}
            >
              <Icon name="menu" />
            </button>
            <span>{isSettings ? "Settings" : "Recordings"}</span>
          </div>
          <span className="local-label" role="status" aria-live="polite" title="Backend health is checked every 10 seconds.">
            <span className={`status-dot connection-${connection}`} aria-hidden="true" /> {connectionLabel}
          </span>
        </header>
        <main id="main-content" className="main-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
