// Tiny hash router: #/overview, #/review, ... No dependency, works on any static host.
import { useEffect, useState } from "react";

export const PAGES = [
  { id: "overview", label: "Overview" },
  { id: "review", label: "Review queue" },
  { id: "conflicts", label: "Conflicts" },
  { id: "partners", label: "Add a partner" },
  { id: "search", label: "Search" },
] as const;
export type PageId = (typeof PAGES)[number]["id"];

function current(): PageId {
  const id = window.location.hash.replace(/^#\/?/, "");
  return (PAGES.find((page) => page.id === id)?.id ?? "overview") as PageId;
}

export function useRoute(): PageId {
  const [page, setPage] = useState<PageId>(current);
  useEffect(() => {
    const onChange = () => setPage(current());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return page;
}
