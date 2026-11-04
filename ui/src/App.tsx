import { useMemo, useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import type { Session } from "./api/client";
import { createConflictsApi } from "./api/conflicts";
import { createDashboardApi } from "./api/dashboard";
import { createReviewQueueApi } from "./api/review";
import { Login } from "./Login";
import { ComingSoon } from "./pages/ComingSoon";
import { Conflicts } from "./pages/Conflicts";
import { Overview } from "./pages/Overview";
import { ReviewQueue } from "./pages/ReviewQueue";
import { useRoute } from "./router";
import { Shell } from "./Shell";

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
  const review = useMemo(() => createReviewQueueApi(session), [session]);
  const conflicts = useMemo(() => createConflictsApi(session), [session]);
  const dashboard = useMemo(() => createDashboardApi(session), [session]);
  const queryClient = useMemo(
    () => new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } }),
    [],
  );
  const page = useRoute();
  return (
    <QueryClientProvider client={queryClient}>
      <Shell page={page} userName={session.name} isDemo={dashboard.isDemo} onSignOut={onSignOut}>
        {page === "overview" && <Overview api={dashboard} />}
        {page === "review" && <ReviewQueue api={review} />}
        {page === "conflicts" && <Conflicts api={conflicts} />}
        {page === "partners" && (
          <ComingSoon
            title="Add a partner"
            step={5}
            description="Upload a spreadsheet, confirm the suggested column mapping, and see live counts."
          />
        )}
        {page === "search" && (
          <ComingSoon
            title="Search"
            step={6}
            description="Search in plain language, such as “I just lost my job and can't pay rent”."
          />
        )}
      </Shell>
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
