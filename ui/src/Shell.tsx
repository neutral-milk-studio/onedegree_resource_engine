import type { ReactNode } from "react";

import { Icon } from "./components/Icon";
import { PAGES, type PageId } from "./router";
import { useTheme, type ThemeChoice } from "./theme";

const THEMES: { id: ThemeChoice; label: string; icon: string }[] = [
  { id: "light", label: "Light", icon: "sun" },
  { id: "dark", label: "Dark", icon: "moon" },
  { id: "system", label: "Match device", icon: "system" },
];

interface ShellProps {
  page: PageId;
  userName: string;
  isDemo: boolean;
  onSignOut(): void;
  children: ReactNode;
}

export function Shell({ page, userName, isDemo, onSignOut, children }: ShellProps) {
  const { choice, choose } = useTheme();
  return (
    <div className="app lg:!flex-row">
      <a
        href="#main"
        className="absolute left-2 top-2 z-50 -translate-y-20 rounded-md bg-primary px-3 py-2 font-semibold text-primary-ink focus:translate-y-0"
      >
        Skip to content
      </a>
      <aside className="flex flex-col gap-3 border-b border-line bg-surface px-4 py-3 lg:w-60 lg:shrink-0 lg:border-b-0 lg:border-r lg:py-5">
        <div className="flex items-center gap-2">
          <span
            className="grid h-8 w-8 place-items-center rounded-lg bg-primary text-sm font-bold text-primary-ink"
            aria-hidden="true"
          >
            RE
          </span>
          <div className="min-w-0">
            <p className="text-base font-semibold leading-tight">Resource Engine</p>
            <p className="text-xs text-muted">One Degree</p>
          </div>
        </div>
        <nav aria-label="Main" className="-mx-1 overflow-x-auto lg:mx-0 lg:overflow-visible">
          <ul className="flex gap-1 lg:flex-col">
            {PAGES.map((item) => {
              const active = item.id === page;
              return (
                <li key={item.id}>
                  <a
                    href={`#/${item.id}`}
                    aria-current={active ? "page" : undefined}
                    className={`flex items-center gap-2 whitespace-nowrap rounded-md px-3 py-2 text-sm no-underline ${
                      active ? "bg-selected font-semibold text-ink" : "text-muted hover:bg-surface-2 hover:text-ink"
                    }`}
                  >
                    <Icon name={item.id} />
                    {item.label}
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="mt-auto hidden flex-col gap-3 lg:flex">
          <ThemePicker choice={choice} choose={choose} />
          <UserBox userName={userName} onSignOut={onSignOut} />
        </div>
      </aside>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-line bg-surface px-4 py-2 lg:hidden">
          <ThemePicker choice={choice} choose={choose} />
          <div className="ml-auto">
            <UserBox userName={userName} onSignOut={onSignOut} />
          </div>
        </div>
        {isDemo && (
          <p className="border-b border-line bg-medium-soft px-4 py-1.5 text-xs text-medium-ink" role="status">
            <strong>Demo data:</strong> numbers and findings are simulated. Set VITE_API_MODE=live to use the API.
          </p>
        )}
        <main id="main" tabIndex={-1} className="app-main overflow-auto">
          {children}
        </main>
      </div>
    </div>
  );
}

function ThemePicker({ choice, choose }: { choice: ThemeChoice; choose(next: ThemeChoice): void }) {
  return (
    <div role="group" aria-label="Color theme" className="inline-flex rounded-lg border border-line p-0.5">
      {THEMES.map((theme) => (
        <button
          key={theme.id}
          type="button"
          aria-pressed={choice === theme.id}
          title={theme.label}
          onClick={() => choose(theme.id)}
          className={`!rounded-md !border-0 !px-2 !py-1 ${choice === theme.id ? "!bg-selected text-ink" : "!bg-transparent text-muted"}`}
        >
          <Icon name={theme.icon} />
          <span className="visually-hidden">{theme.label}</span>
        </button>
      ))}
    </div>
  );
}

function UserBox({ userName, onSignOut }: { userName: string; onSignOut(): void }) {
  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="truncate" title={userName}>
        {userName}
      </span>
      <button type="button" onClick={onSignOut} className="!px-2 !py-1 text-xs">
        Sign out
      </button>
    </div>
  );
}
