import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { ApiError } from "../api/client";
import { createMockReviewApi, formatValue, type ReviewQueueApi } from "../api/review";
import { ReviewQueue } from "./ReviewQueue";

function renderWith(api: ReviewQueueApi) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ReviewQueue api={api} />
    </QueryClientProvider>,
  );
}

describe("ReviewQueue", () => {
  it("lists findings by harm and opens the most harmful first", async () => {
    renderWith(createMockReviewApi("Sam"));
    await screen.findByRole("article");
    const list = screen.getByRole("region", { name: "Findings" });
    const names = within(list)
      .getAllByRole("button")
      .map((b) => b.querySelector("span.font-medium")?.textContent);
    expect(names).toEqual(["Demo Pantry", "Demo Rent Grants", "Demo Clinic", "Demo Legal Aid"]);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Demo Pantry");
    expect(screen.getByText("The website looks gone.")).toBeInTheDocument();
  });

  it("filters by category with counts", async () => {
    const user = userEvent.setup();
    renderWith(createMockReviewApi("Sam"));
    const filters = await screen.findByRole("group", { name: "Filter by category" });
    expect(await within(filters).findByRole("button", { name: /All 4/ })).toHaveAttribute("aria-pressed", "true");
    await user.click(within(filters).getByRole("button", { name: /Doesn't match website 1/ }));
    const list = screen.getByRole("region", { name: "Findings" });
    expect(await within(list).findAllByRole("button")).toHaveLength(1);
    expect(within(list).getByText("Demo Clinic")).toBeInTheDocument();
  });

  it("shows current vs proposed with the quote, applies, and shows the audit entry", async () => {
    const user = userEvent.setup();
    const api = createMockReviewApi("Sam");
    const apply = vi.spyOn(api, "apply");
    renderWith(api);
    await user.click(await screen.findByRole("button", { name: /Demo Clinic/ }));

    const detail = screen.getByRole("article");
    expect(within(detail).getByText("In the record now")).toBeInTheDocument();
    expect(within(detail).getByText(/Call our new line at \(415\) 555-0199/)).toBeInTheDocument();
    expect(within(detail).getByRole("link", { name: /clinic\.example/ })).toHaveAttribute("target", "_blank");

    await user.click(within(detail).getByRole("button", { name: "Apply website value" }));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ id: "demo-2" }), undefined, expect.any(Number));
    const saved = await screen.findByRole("status");
    expect(saved).toHaveTextContent("Saved");
    expect(saved).toHaveTextContent("Sam updated phone on “Demo Clinic”");
    expect(screen.queryByText("Demo Clinic", { selector: "span" })).not.toBeInTheDocument();
  });

  it("asks for a status on a dead site and can keep the current value", async () => {
    const user = userEvent.setup();
    const api = createMockReviewApi("Sam");
    const apply = vi.spyOn(api, "apply");
    renderWith(api);
    const detail = await screen.findByRole("article");
    await user.selectOptions(within(detail).getByRole("combobox"), "defunct");
    await user.click(within(detail).getByRole("button", { name: "Apply status change" }));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ id: "demo-1" }), "defunct", expect.any(Number));

    await user.click(await screen.findByRole("button", { name: /Demo Clinic/ }));
    await user.click(within(screen.getByRole("article")).getByRole("button", { name: /^Keep/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("Kept the current value");
  });

  it("shows validation problems from the server and saves nothing", async () => {
    const user = userEvent.setup();
    const api = createMockReviewApi("Sam");
    vi.spyOn(api, "apply").mockRejectedValue(
      new ApiError(422, "The change didn't pass HSDS validation, so nothing was saved.", [
        { path: "phones/0/number", message: "is required" },
      ]),
    );
    renderWith(api);
    await user.click(await screen.findByRole("button", { name: /Demo Clinic/ }));
    await user.click(screen.getByRole("button", { name: "Apply website value" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("nothing was saved");
    expect(alert).toHaveTextContent("phones/0/number: is required");
  });

  it("times how long the fix takes", async () => {
    // Real time, not fake timers: the timer must move past 0 within a couple of seconds.
    renderWith(createMockReviewApi("Sam"));
    expect(await screen.findByText("Working on this: 0 s")).toBeInTheDocument();
    expect(await screen.findByText(/Working on this: [1-9] s/, {}, { timeout: 3000 })).toBeInTheDocument();
  });
});

describe("ReviewQueue suspicious listings", () => {
  async function openSuspiciousDetail(api: ReviewQueueApi) {
    const user = userEvent.setup();
    renderWith(api);
    await user.click(await screen.findByRole("button", { name: /Demo Rent Grants/ }));
    return { user, detail: screen.getByRole("article") };
  }

  it("lists the signals and score as 'flagged for review', never 'scam'", async () => {
    const { detail } = await openSuspiciousDetail(createMockReviewApi("Sam"));
    expect(detail).toHaveTextContent("Flagged for review");
    expect(detail).not.toHaveTextContent(/\bscam\b/i);
    expect(detail).not.toHaveTextContent(/\bfraud\b/i);
    expect(within(detail).getByText("Asks for a $99 fee before helping")).toBeInTheDocument();
  });

  it("'Hide from search' calls the hide action, not apply or dismiss", async () => {
    const api = createMockReviewApi("Sam");
    const hide = vi.spyOn(api, "hide");
    const { user, detail } = await openSuspiciousDetail(api);
    await user.click(within(detail).getByRole("button", { name: "Hide from search" }));
    expect(hide).toHaveBeenCalledWith(expect.objectContaining({ id: "demo-4" }), expect.any(Number));
  });

  it("'Mark safe' is disabled until a note is entered, then calls markSafe with it", async () => {
    const api = createMockReviewApi("Sam");
    const markSafe = vi.spyOn(api, "markSafe");
    const { user, detail } = await openSuspiciousDetail(api);
    const markSafeButton = within(detail).getByRole("button", { name: "Mark safe" });
    expect(markSafeButton).toBeDisabled();
    await user.type(within(detail).getByLabelText(/Note for.*Mark safe/), "Called them, this is real");
    expect(markSafeButton).toBeEnabled();
    await user.click(markSafeButton);
    expect(markSafe).toHaveBeenCalledWith(
      expect.objectContaining({ id: "demo-4" }),
      "Called them, this is real",
      expect.any(Number),
    );
  });

  it("'Confirm and remove' applies the inactive status", async () => {
    const api = createMockReviewApi("Sam");
    const apply = vi.spyOn(api, "apply");
    const { user, detail } = await openSuspiciousDetail(api);
    await user.click(within(detail).getByRole("button", { name: "Confirm and remove" }));
    expect(apply).toHaveBeenCalledWith(expect.objectContaining({ id: "demo-4" }), "inactive", expect.any(Number));
  });
});

describe("formatValue", () => {
  it("formats hours, lists and empty values in plain language", () => {
    expect(formatValue([{ byday: "MO,TU", opens_at: "08:30", closes_at: "17:00" }])).toBe("Mon, Tue: 8:30 AM – 5:00 PM");
    expect(formatValue(["English", "Spanish"])).toBe("English, Spanish");
    expect(formatValue(null)).toBe("Not in the record");
  });
});
