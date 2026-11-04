import { useState, type FormEvent } from "react";

import { ApiError, signIn, type Session } from "./api/client";
import { DEMO_ACCOUNTS } from "./api/mock";

const isDemo = import.meta.env.VITE_API_MODE !== "live";

export function Login({ onSignedIn }: { onSignedIn(session: Session): void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await signIn(email, password));
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Couldn't sign you in. Try again.");
      setBusy(false);
    }
  };

  return (
    <main className="login">
      <div className="login-card">
        <p className="login-mark" aria-hidden="true">
          RR
        </p>
        <h1>Resource Refresh</h1>
        <p className="muted">Sign in to review community resource records.</p>
        <form onSubmit={(event) => void submit(event)}>
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            autoFocus
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
          {error && (
            <p className="alert" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        {isDemo && (
          <div className="demo-accounts">
            <p>
              <strong>Demo accounts.</strong> Password: <code>{DEMO_ACCOUNTS[0].password}</code>
            </p>
            {DEMO_ACCOUNTS.map((account) => (
              <button
                key={account.email}
                type="button"
                onClick={() => {
                  setEmail(account.email);
                  setPassword(account.password);
                }}
              >
                Use {account.name}
              </button>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
