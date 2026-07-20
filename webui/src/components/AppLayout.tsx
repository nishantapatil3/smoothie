import { useState, useEffect, useCallback } from "react";
import { Outlet, Link, useNavigate, useLocation } from "react-router-dom";
import { getMe, logout } from "../api";
import { VITE_WEBUI_VERSION } from "../api/config";
import { cn } from "../lib/utils";

export default function AppLayout() {
  const [user, setUser] = useState<{ username: string; is_admin: boolean; tier: string; tier_remaining_minutes?: number | null } | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const view: "recordings" | "settings" = location.pathname.startsWith("/settings")
    ? "settings"
    : "recordings";

  const loadUser = useCallback(async () => {
    try {
      const data = await getMe();
      setUser(data);
    } catch {
      navigate("/login");
    }
  }, [navigate]);

  useEffect(() => {
    loadUser();
  }, [loadUser]);

  async function handleLogout() {
    await logout();
    navigate("/login");
  }

  if (!user) return <div className="min-h-screen flex items-center justify-center">Loading…</div>;

  const tierLabels = {
    free: "Free",
    standard: "Standard",
    pro: "Pro",
  } as const;

  const tierColors = {
    free: "badge-free",
    standard: "badge-standard",
    pro: "badge-pro",
  } as const;

  return (
    <div className="h-screen bg-gray-50 flex flex-col overflow-hidden">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <h1 className="text-xl font-bold text-gray-900">🥤 Smoothie</h1>
              <span className="text-xs text-gray-400 hidden sm:inline">v{VITE_WEBUI_VERSION}</span>
            </div>

            <div className="flex items-center gap-2 sm:gap-3 min-w-0">
              <span className={cn("badge", tierColors[user.tier as keyof typeof tierColors])}>
                {tierLabels[user.tier as keyof typeof tierLabels]}
                {user.tier === "free" && user.tier_remaining_minutes != null && (
                  <span className="ml-1">· {user.tier_remaining_minutes}m left</span>
                )}
              </span>

              <button onClick={() => setSidebarOpen(!sidebarOpen)} className="btn btn-ghost text-sm md:hidden">
                ☰
              </button>

              <span className="text-sm text-gray-500 truncate max-w-[8rem] sm:max-w-[12rem] hidden sm:inline">
                {user.username}
              </span>
              <button onClick={handleLogout} className="btn btn-ghost text-sm whitespace-nowrap">
                Log out
              </button>
            </div>
          </div>
        </div>

        {sidebarOpen && (
          <div className="md:hidden border-t border-gray-200">
            <nav className="px-4 py-3 space-y-2">
              <button onClick={() => navigate("/recordings")} className="w-full text-left p-2 rounded-lg hover:bg-gray-100">
                🏠 Recordings
              </button>
              {user.is_admin && (
                <button onClick={() => navigate("/settings")} className="w-full text-left p-2 rounded-lg hover:bg-gray-100">
                  ⚙ Settings
                </button>
              )}
            </nav>
          </div>
        )}
      </header>

      <div className="flex-1 flex overflow-hidden">
        <aside
          className={cn(
            "w-64 bg-white border-r border-gray-200 flex-shrink-0 hidden md:block",
            view === "settings" && "hidden"
          )}
        >
          <nav className="p-4 space-y-1">
            <Link
              to="/recordings"
              className={cn(
                "block px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                location.pathname === "/recordings"
                  ? "bg-primary-50 text-primary-700"
                  : "text-gray-600 hover:bg-gray-50"
              )}
            >
              🏠 Recordings
            </Link>
            {user.is_admin && (
              <Link
                to="/settings"
                className={cn(
                  "block px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                  location.pathname === "/settings"
                    ? "bg-primary-50 text-primary-700"
                    : "text-gray-600 hover:bg-gray-50"
                )}
              >
                ⚙ Settings
              </Link>
            )}
          </nav>
        </aside>

        <main className="flex-1 overflow-auto">
          <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}