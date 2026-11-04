// Light / dark / system theme. The resolved theme is the data-theme attribute on <html>.
import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "system" | "light" | "dark";
const KEY = "re-theme";

function readChoice(): ThemeChoice {
  try {
    const saved = localStorage.getItem(KEY);
    return saved === "light" || saved === "dark" ? saved : "system";
  } catch {
    return "system";
  }
}

function systemPrefersDark(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(choice: ThemeChoice): "light" | "dark" {
  const resolved = choice === "system" ? (systemPrefersDark() ? "dark" : "light") : choice;
  document.documentElement.setAttribute("data-theme", resolved);
  return resolved;
}

export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(readChoice);
  const [resolved, setResolved] = useState<"light" | "dark">(() => applyTheme(readChoice()));

  useEffect(() => {
    setResolved(applyTheme(choice));
    if (choice !== "system" || typeof window.matchMedia !== "function") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => setResolved(applyTheme("system"));
    media.addEventListener?.("change", onChange);
    return () => media.removeEventListener?.("change", onChange);
  }, [choice]);

  const choose = useCallback((next: ThemeChoice) => {
    try {
      if (next === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, next);
    } catch {
      // The choice still applies for this page view.
    }
    setChoice(next);
  }, []);

  return { choice, resolved, choose };
}
