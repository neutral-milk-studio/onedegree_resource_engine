import type { RecordSummary, ReviewFinding } from "../../api/client";
import { createMockApi } from "../../api/mock";
import { buildRows, describeFinding, filterRows, rowCategory, type QueueFilters } from "./queue";

const base: QueueFilters = { view: "flagged", tier: "", category: "", source: "", search: "" };

async function rows() {
  const api = createMockApi();
  return buildRows(await api.listFlagged(), await api.listAllFindings(), await api.listClashes());
}

describe("review queue", () => {
  it("puts actively bad records first and ranks by score", async () => {
    const all = await rows();
    expect(rowCategory(all[0])).toBe("actively_bad");
    expect(all[0].tier).toBe("urgent");
    const scores = all.map((row) => row.score);
    expect(scores).toEqual([...scores].sort((a, b) => b - a));
  });

  it("explains every priority", async () => {
    const flagged = filterRows(await rows(), base);
    expect(flagged.length).toBeGreaterThan(0);
    expect(flagged.every((row) => row.reasons.length > 0)).toBe(true);
  });

  it("ranks an urgent service above an otherwise equal one", () => {
    const check = { service_id: "x", category: "missing_information", checked_at: "2026-11-03T16:00:00Z", finding_ids: [] };
    const make = (id: string, name: string) =>
      ({ id, name, source: "ndp", status: "active", version: 1, open_findings: 0, organization_name: null, last_check: check }) as RecordSummary;
    const [first, second] = buildRows([make("a", "Book club"), make("b", "Overnight shelter")], []);
    expect(first.name).toBe("Overnight shelter");
    expect(first.score).toBeGreaterThan(second.score);
    expect(first.tier).toBe("medium");
    expect(second.tier).toBe("low");
  });

  it("lists every source involved on a record with evidence", async () => {
    const row = (await rows()).find((item) => item.organization_name === "Coastside Hope" && item.source === "ndp");
    expect(row?.sources).toEqual(expect.arrayContaining(["ndp", "website"]));
  });

  it("names the missing fields", async () => {
    const row = (await rows()).find((item) => item.issue.startsWith("Missing "));
    expect(row?.issue).toMatch(/hours|phone|eligibility/);
  });

  it("filters by priority", async () => {
    const all = await rows();
    expect(filterRows(all, { ...base, tier: "urgent" }).every((row) => row.tier === "urgent")).toBe(true);
  });

  it("hides verified and unchecked records from the flagged view", async () => {
    const all = await rows();
    const flagged = filterRows(all, base);
    expect(flagged.some((row) => rowCategory(row) === "verified")).toBe(false);
    expect(flagged.some((row) => !row.tier)).toBe(false);
    expect(filterRows(all, { ...base, view: "all" })).toHaveLength(all.length);
  });

  it("filters by category, source, and search together", async () => {
    const all = await rows();
    const result = filterRows(all, { ...base, category: "discrepancy", source: "ndp", search: "coastside" });
    expect(result).toHaveLength(1);
    expect(result[0].organization_name).toBe("Coastside Hope");
  });

  it("stops flagging a record after its only finding is decided", () => {
    const record = {
      id: "r1",
      name: "Service",
      source: "ndp",
      status: "active",
      version: 1,
      open_findings: 0,
      organization_name: null,
      last_check: { service_id: "r1", category: "discrepancy", checked_at: "2026-11-03T16:00:00Z", finding_ids: [] },
    } as RecordSummary;
    const [row] = buildRows([record], []);
    expect(row.issue).toBe("Reviewed");
    expect(row.tier).toBeNull();
    expect(rowCategory(row)).toBeUndefined();
  });

  it("describes a dead site from its failure", () => {
    const finding = {
      category: "actively_bad",
      ref: { field: "status", entity_id: "x", entity_type: "service", op: "set" },
      evidence: [{ kind: "http_signal", http_status: 404 }],
    } as unknown as ReviewFinding;
    expect(describeFinding(finding)).toBe("Site problem: HTTP 404");
  });
});
