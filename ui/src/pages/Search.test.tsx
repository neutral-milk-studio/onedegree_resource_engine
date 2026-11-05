import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { createMockSearchApi } from "../api/search";
import { Search } from "./Search";

describe("Search", () => {
  it("searches in plain language and explains the results", async () => {
    const user = userEvent.setup();
    const api = createMockSearchApi();
    const spy = vi.spyOn(api, "search");
    render(<Search api={api} />);
    await user.click(screen.getByRole("button", { name: "I just lost my job and can't pay rent" }));
    expect(spy).toHaveBeenCalledWith("I just lost my job and can't pay rent", 0, 10);

    const results = await screen.findByRole("region", { name: /2 services for/ });
    expect(results).toHaveTextContent("so we also searched for housing, financial assistance");
    expect(results).toHaveTextContent("1 matching listing is not shown because it is flagged for review");
    const [first] = within(results).getAllByRole("article");
    expect(first).toHaveTextContent("Verified today");
    expect(within(first).getAllByText("rent", { selector: "mark" })).toHaveLength(2);
    expect(within(first).getByRole("link", { name: /Call \(510\) 555-0100/ })).toHaveAttribute("href", "tel:5105550100");
  });

  it("never renders snippet markup as HTML", async () => {
    const user = userEvent.setup();
    const api = createMockSearchApi();
    vi.spyOn(api, "search").mockResolvedValue({
      query: "x", needs: [], own_terms: ["x"], added_terms: [], total: 1, hidden: 0, backend: "demo",
      results: [{
        service_id: "s", name: "S", organization_name: null, city: null, phone: null, website: null, source: "ndp", score: 1,
        latitude: null, longitude: null,
        snippet: "<img src=x onerror=alert(1)> «food»", trust: { score: null, level: "unknown", badge: "Not checked yet", checked_at: null },
      }],
    });
    render(<Search api={api} />);
    await user.type(screen.getByLabelText("What do you need help with?"), "food{Enter}");
    const article = await screen.findByRole("article");
    expect(article.querySelector("img")).toBeNull();
    expect(article).toHaveTextContent("<img src=x onerror=alert(1)>");
  });

  it("shows a map when results have coordinates", async () => {
    const user = userEvent.setup();
    render(<Search api={createMockSearchApi()} />);
    await user.click(screen.getByRole("button", { name: "I just lost my job and can't pay rent" }));
    expect(await screen.findByRole("group", { name: /Map of 2 results/ })).toBeInTheDocument();
  });

  it("requires at least two letters", async () => {
    render(<Search api={createMockSearchApi()} />);
    expect(screen.getByRole("button", { name: "Search" })).toBeDisabled();
  });
});
