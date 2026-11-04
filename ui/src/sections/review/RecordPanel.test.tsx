import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { createMockApi } from "../../api/mock";
import { RecordPanel } from "./RecordPanel";

// The grid needs a real browser, and its behavior is covered by the mock API tests.
vi.mock("./FieldsEditor", () => ({ FieldsEditor: () => <div>Field editor</div> }));

async function setup(category: string, withProposal = true) {
  const api = createMockApi();
  const finding = (await api.listAllFindings()).find(
    (item) => item.category === category && Boolean(item.proposed_value) === withProposal,
  );
  if (!finding) throw new Error(`No ${category} finding`);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <RecordPanel api={api} recordId={finding.service_id} />
    </QueryClientProvider>,
  );
  return { api, finding, user: userEvent.setup() };
}

describe("RecordPanel", () => {
  it("shows current, proposed, and evidence", async () => {
    const { finding } = await setup("discrepancy");
    const card = await screen.findByRole("article", { name: /adds|differs/ });
    expect(within(card).getByText(finding.evidence[0].snippet as string)).toBeInTheDocument();
    expect(within(card).getByRole("link", { name: /Open source/ })).toHaveAttribute("href", finding.evidence[0].url);
    expect(within(card).getByText("Website")).toBeInTheDocument();
    expect(within(card).getByText(/Checked Nov 3, 2026/)).toBeInTheDocument();
    expect(card.querySelector("ins")).toBeTruthy();
  });

  it("applying a finding saves and adds it to the change history", async () => {
    const { user } = await setup("discrepancy");
    await screen.findByRole("heading", { name: "Findings" });
    expect(screen.getByText("No changes yet.")).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: /Apply website value/ })[0]);

    expect(await screen.findByText(/Saved in/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/changed by Demo reviewer/)).toBeInTheDocument());
    expect(screen.queryByText("No changes yet.")).not.toBeInTheDocument();
  });

  it("dismissing a finding leaves the record unchanged", async () => {
    const { user } = await setup("discrepancy");
    await screen.findByRole("heading", { name: "Findings" });
    await user.click(screen.getAllByRole("button", { name: "Dismiss" })[0]);
    expect(await screen.findByText("dismissed")).toBeInTheDocument();
    expect(screen.getByText("No changes yet.")).toBeInTheDocument();
  });

  it("a dead site offers no one-click apply, and a person sets the status", async () => {
    const { user } = await setup("actively_bad", false);
    const card = await screen.findByRole("article", { name: /Site problem/ });
    expect(within(card).queryByRole("button", { name: /Apply/ })).not.toBeInTheDocument();

    await user.click(within(card).getByRole("button", { name: "Set status" }));
    await user.selectOptions(within(card).getByLabelText(/New value/), "inactive");
    await user.click(within(card).getByRole("button", { name: "Save change" }));

    expect(await screen.findByText("edited")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/changed by Demo reviewer/)).toBeInTheDocument());
  });

  it("keyboard apply asks for confirmation first", async () => {
    const { api, finding, user } = await setup("discrepancy");
    await screen.findByRole("heading", { name: "Findings" });

    await user.keyboard("a");
    const dialog = screen.getByRole("alertdialog");
    expect((await api.getFindings(finding.service_id)).version).toBe(1);

    await user.click(within(dialog).getByRole("button", { name: "Apply" }));
    await waitFor(async () => expect((await api.getFindings(finding.service_id)).version).toBe(2));
  });

  it("a rejected edit in the record editor doesn't change the record", async () => {
    const { api, finding } = await setup("discrepancy");
    await screen.findByRole("heading", { name: "Findings" });
    const { version } = await api.getFindings(finding.service_id);
    await expect(
      api.patchRecord(finding.service_id, version, [
        { ref: { entity_type: "service", entity_id: finding.service_id, field: "name", op: "set" }, value: null },
      ]),
    ).rejects.toMatchObject({ status: 422 });
    expect((await api.getFindings(finding.service_id)).version).toBe(version);
  });

  it("a stale version shows an error and saves nothing", async () => {
    const { api, finding, user } = await setup("discrepancy");
    await screen.findByRole("heading", { name: "Findings" });
    const { version } = await api.getFindings(finding.service_id);
    await api.patchRecord(finding.service_id, version, [
      { ref: { entity_type: "service", entity_id: finding.service_id, field: "wait_time", op: "set" }, value: "1 week" },
    ]);

    await user.click(screen.getAllByRole("button", { name: /Apply website value/ })[0]);

    expect(await screen.findByRole("alert")).toHaveTextContent(/Reload it and try again/);
    expect((await api.getFindings(finding.service_id)).findings.find((item) => item.id === finding.id)?.review_state).toBe("pending");
  });
});
