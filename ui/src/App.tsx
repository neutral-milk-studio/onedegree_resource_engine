import { useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { createApi, type Session } from "./api/client";
import { Login } from "./Login";
import { RecordsReview } from "./sections/RecordsReview";

const SESSION_KEY = "resource-refresh-session";

function loadSession(): Session | null {
  try {
    const saved = sessionStorage.getItem(SESSION_KEY);
    return saved ? (JSON.parse(saved) as Session) : null;
  } catch {
    return null;
  }
}

function saveSession(session: Session | null): void {
  try {
    if (session) sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
    else sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // The session still works for this page view without storage.
  }
}

function Workspace({ session, onSignOut }: { session: Session; onSignOut(): void }) {
  const api = useMemo(() => createApi(session), [session]);
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } }),
    [],
  );
  return (
    <QueryClientProvider client={queryClient}>
      <div className="app">
        <header className="app-header">
          <h1>Resource Refresh</h1>
          {api.isDemo && (
            <p className="demo-note" role="status">
              <strong>Demo data:</strong> findings and clashes are simulated, not claims about real organizations.
            </p>
          )}
          <div className="user">
            <span>{session.name}</span>
            <button type="button" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </header>
        <main className="app-main">
          <RecordsReview api={api} />
        </main>
      </div>
    </QueryClientProvider>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(loadSession);
  const update = (next: Session | null) => {
    saveSession(next);
    setSession(next);
  };
  if (!session) return <Login onSignedIn={update} />;
  return <Workspace session={session} onSignOut={() => update(null)} />;
}
