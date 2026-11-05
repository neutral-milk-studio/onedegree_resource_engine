import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { createMockDashboardApi, type DashboardApi, type Metrics } from "../api/dashboard";
import { Overview } from "./Overview";

function renderWith(api: DashboardApi) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Overview api={api} />
    </QueryClientProvider>,
  );
}

const METRICS: Metrics = {
  generated_at: "2026-11-04T00:00:00Z",
  total_records: 86,
  records_by_source: [
    { source: "ndp", records: 36 },
    { source: "odrs", records: 50 },
  ],
  records_by_status: { active: 80, inactive: 6 },
  quarantined_records: 1,
  records_missing_information: 50,
  quarantined_objects: 7,
  last_ingest: [],
  findings: {
    checks_run: 10,
    records_checked: 10,
    decided: 2,
    median_seconds_to_fix: 18,
    model_ids: ["anthropic.test-model"],
    open_total: 5,
    open_by_category: {
      actively_bad: 1,
      suspicious: 1,
      discrepancy: 2,
      missing_information: 1,
      check_failed: 0,
      verified: 0,
    },
  },
  conflicts_open: 2,
  avg_trust_score: 72.4,
  cost_per_check_usd: 0.0031,
};

const staticApi = (metrics: Metrics): DashboardApi => ({
  isDemo: false,
  getMetrics: async () => metrics,
  getAudit: async () => [
    {
      id: "a1",
      at: new Date().toISOString(),
      actor: "dev-reviewer",
      action: "ingest_run",
      change_source: "ingest",
      entity_type: "source",
      entity_id: "odrs",
      field: null,
      old_value: null,
      new_value: null,
      summary: "Ingested odrs: 50 received, 50 stored, 0 quarantined",
    },
  ],
  getRuns: async () => [],
  runIngest: async () => {
    throw new Error("not used");
  },
});

describe("Overview", () => {
  it("shows the headline numbers from the API", async () => {
    renderWith(staticApi(METRICS));
    const tiles = await screen.findByRole("region", { name: "Key numbers" });
    expect(within(tiles).getByText("Total records").parentElement).toHaveTextContent("86");
    expect(within(tiles).getByText("One Degree (ODRS)")).toBeInTheDocument();
    expect(within(tiles).getByText("Flagged for review").parentElement).toHaveTextContent("1");
    expect(within(tiles).getByText("Conflicts between sources").parentElement).toHaveTextContent("2");
    expect(within(tiles).getByText("Average trust score").parentElement).toHaveTextContent("72 / 100");
    expect(within(tiles).getByText("Cost per AI check").parentElement).toHaveTextContent("$0.0031");
    expect(screen.getByText(/50 received, 50 stored/)).toBeInTheDocument();
  });

  it("draws findings bars in harm order with a table view", async () => {
    const user = userEvent.setup();
    renderWith(staticApi(METRICS));
    const chart = await screen.findByRole("list", { name: "Findings by category" });
    const labels = within(chart)
      .getAllByRole("img")
      .map((bar) => bar.getAttribute("aria-label"));
    expect(labels[0]).toBe("Actively bad: 1 open");
    expect(labels[2]).toBe("Doesn't match website: 2 open");
    expect(screen.queryByTestId("bar-check_failed")).not.toBeInTheDocument(); // zero draws no bar

    await user.click(screen.getByRole("button", { name: "Show table" }));
    expect(screen.getByRole("table")).toHaveTextContent("Missing information1");
  });

  it("says 'not run yet' instead of showing misleading zeros", async () => {
    renderWith(createMockDashboardApi("Sam"));
    expect(await screen.findByText(/No website checks have run yet. Findings appear/)).toBeInTheDocument();
    expect(screen.getByText("Not checked yet.")).toBeInTheDocument();
    expect(screen.getByText("Shown after the first website check.")).toBeInTheDocument();
  });

  it("runs an ingest and refreshes the numbers and activity", async () => {
    const user = userEvent.setup();
    renderWith(createMockDashboardApi("Sam"));
    const sources = await screen.findByRole("region", { name: "Sources" });
    await user.click(within(sources).getAllByRole("button", { name: "Run now" })[0]);

    expect(await screen.findByRole("status")).toHaveTextContent("50 received, 50 stored, 0 quarantined");
    const tiles = screen.getByRole("region", { name: "Key numbers" });
    await vi.waitFor(() =>
      expect(within(tiles).getByText("Total records").parentElement).toHaveTextContent("Total records50"),
    );
    expect(await screen.findByText(/Ingested odrs/)).toBeInTheDocument();
  });

  it("explains a failed load", async () => {
    const api = { ...staticApi(METRICS), getMetrics: async () => Promise.reject(new Error("boom")) };
    renderWith(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load the dashboard");
  });
});
