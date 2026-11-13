import { useState } from "react";
import { Outlet, Link, useLocation } from "react-router-dom";
import { VITE_WEBUI_VERSION } from "../api/config";
import { cn } from "../lib/utils";

const navigation = [
  { path: "/recordings", label: "🏠 Recordings" },
  { path: "/settings", label: "⚙ Settings" },
];

export default function AppLayout() {
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const location = useLocation();

  return (
    <div className="h-screen bg-gray-50 flex flex-col overflow-hidden">
      <header className="bg-white border-b border-gray-200 sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-4">
              <Link to="/" className="text-xl font-bold text-gray-900 hover:opacity-80">
                🥤 Smoothie
              </Link>
              <span className="text-xs text-gray-400 hidden sm:inline">v{VITE_WEBUI_VERSION}</span>
            </div>
            <div className="flex items-center gap-2 sm:gap-3">
              <button onClick={() => setSidebarOpen(!sidebarOpen)} className="btn btn-ghost text-sm md:hidden" aria-label="Toggle navigation">
                ☰
              </button>
              <Link to="/settings" className="btn btn-ghost text-sm whitespace-nowrap">
                ⚙ Settings
              </Link>
            </div>
          </div>
        </div>
        {sidebarOpen && (
          <nav className="md:hidden border-t border-gray-200 px-4 py-3 space-y-2">
            {navigation.map(({ path, label }) => (
              <Link key={path} to={path} onClick={() => setSidebarOpen(false)} className="block p-2 rounded-lg hover:bg-gray-100">
                {label}
              </Link>
            ))}
          </nav>
        )}
      </header>
      <div className="flex-1 flex overflow-hidden">
        <aside className="w-64 bg-white border-r border-gray-200 flex-shrink-0 hidden md:block">
          <nav className="p-4 space-y-1">
            {navigation.map(({ path, label }) => (
              <Link
                key={path}
                to={path}
                className={cn(
                  "block px-3 py-2 rounded-lg text-sm font-medium transition-colors",
                  location.pathname.startsWith(path)
                    ? "bg-primary-50 text-primary-700"
                    : "text-gray-600 hover:bg-gray-50"
                )}
              >
                {label}
              </Link>
            ))}
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
