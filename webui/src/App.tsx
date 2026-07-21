import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Login from "./components/Login";
import AppLayout from "./components/AppLayout";
import RecordingsList from "./components/RecordingsList";
import TranscriptView from "./components/TranscriptView";
import Settings from "./components/Settings";
import Subscriptions from "./components/Subscriptions";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5000,
      retry: 1,
    },
  },
});

function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<AppLayout />}>
        <Route index element={<Navigate to="/recordings" replace />} />
        <Route path="/recordings" element={<RecordingsList />} />
        <Route path="/recordings/:id" element={<TranscriptView />} />
        <Route path="/settings" element={<Settings onTierChanged={() => {}} />} />
        <Route path="/subscriptions" element={<Subscriptions onTierChanged={() => {}} />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AppRoutes />
      </BrowserRouter>
    </QueryClientProvider>
  );
}