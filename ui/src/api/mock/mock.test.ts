import { ApiError } from "../client";
import { createMockApi, MOCK_REVIEWER, validateRecord } from ".";

const firstFinding = async (category: string) => {
  const api = createMockApi();
  const findings = await api.listAllFindings();
  const finding = findings.find((item) => item.category === category && item.proposed_value);
  if (!finding) throw new Error(`No ${category} finding with a proposal`);
  const detail = await api.getFindings(finding.service_id);
  return { api, finding, detail };
};

describe("mock review API", () => {
  it("applying writes the value and a history entry, and bumps the version", async () => {
    const { api, finding, detail } = await firstFinding("discrepancy");
    const result = await api.applyChange(finding.id, detail.version);
    expect(result.review_state).toBe("applied");

    const updated = await api.getFindings(finding.service_id);
    expect(updated.version).toBe(detail.version + 1);
    const history = updated.record.metadata as { updated_by: string; field_name: string }[];
    expect(history.at(-1)).toMatchObject({ updated_by: MOCK_REVIEWER, field_name: finding.ref.field });
  });

  it("applying an edited value marks the finding as edited", async () => {
    const { api, finding, detail } = await firstFinding("discrepancy");
    const value = finding.ref.op === "add" ? { name: "Edited" } : "Edited";
    const result = await api.applyChange(finding.id, detail.version, value);
    expect(result.review_state).toBe("edited");
  });

  it("rejects a stale version and saves nothing", async () => {
    const { api, finding, detail } = await firstFinding("discrepancy");
    await expect(api.applyChange(finding.id, detail.version - 1)).rejects.toMatchObject({ status: 409 });
    const unchanged = await api.getFindings(finding.service_id);
    expect(unchanged.version).toBe(detail.version);
    expect(unchanged.findings.find((item) => item.id === finding.id)?.review_state).toBe("pending");
  });

  it("blocks an invalid edit, names the field, and saves nothing", async () => {
    const api = createMockApi();
    const [row] = await api.listFlagged();
    const detail = await api.getFindings(row.id);
    const change = { ref: { entity_type: "service", entity_id: row.id, field: "status", op: "set" as const }, value: "open" };

    const failure = await api.patchRecord(row.id, detail.version, [change]).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(422);
    expect((failure as ApiError).problems[0].path).toBe("status");
    expect((await api.getFindings(row.id)).record.status).toBe(detail.record.status);
  });

  it("needs a value when a finding has no proposal", async () => {
    const api = createMockApi();
    const finding = (await api.listAllFindings()).find((item) => item.category === "actively_bad");
    if (!finding) throw new Error("No actively bad finding");
    const { version } = await api.getFindings(finding.service_id);
    await expect(api.applyChange(finding.id, version)).rejects.toMatchObject({ status: 422 });
    expect((await api.applyChange(finding.id, version, "inactive")).review_state).toBe("edited");
  });

  it("resolving a clash keeps the chosen value", async () => {
    const api = createMockApi();
    const records = await api.listFlagged();
    const details = await Promise.all(records.map((row) => api.getFindings(row.id)));
    const detail = details.find((item) => item.clashes.length);
    if (!detail) throw new Error("No clash");
    const [clash] = detail.clashes;
    const result = await api.resolveClash(clash.id, detail.version, "current");
    expect(result.status).toBe("resolved");
    const updated = await api.getFindings(clash.service_id);
    expect(updated.record.eligibility_description).toBe(clash.current_value);
  });

  it("every demo record passes the mock validation", async () => {
    const api = createMockApi();
    for (const row of await api.listFlagged()) {
      expect(validateRecord((await api.getFindings(row.id)).record)).toEqual([]);
    }
  });
});
