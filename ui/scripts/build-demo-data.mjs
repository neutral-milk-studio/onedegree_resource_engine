// Builds src/api/mock/demo-data.json for the review tool's mock mode.
// Records come from the NDP and ODRS samples, the seed set, and the fixtures. Findings are
// simulated, and they only sit on the fixtures and on fictional organizations.
// Gaps marked as missing information are real gaps in the sample records.
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "../..");
const read = (path) => JSON.parse(readFileSync(resolve(root, path), "utf8"));

const NAMESPACE = "6f3b9c2a1e5d5a7b9c4e0a1b2c3d4e5f";
const CHECKED_AT = "2026-11-03T16:00:00Z";
const OFFSET = "-08:00";
const DAYS = { mon: "MO", tue: "TU", wed: "WE", thu: "TH", fri: "FR", sat: "SA", sun: "SU" };

function uuid(source, type, nativeId) {
  const hash = createHash("sha1")
    .update(Buffer.from(NAMESPACE, "hex"))
    .update(`${source}:${type}:${nativeId}`)
    .digest("hex");
  const variant = ((parseInt(hash[16], 16) & 3) | 8).toString(16);
  return `${hash.slice(0, 8)}-${hash.slice(8, 12)}-5${hash.slice(13, 16)}-${variant}${hash.slice(17, 20)}-${hash.slice(20, 32)}`;
}

const stripHtml = (text) =>
  text ? text.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : undefined;

function compact(value) {
  if (Array.isArray(value)) return value.map(compact);
  if (value && typeof value === "object") {
    const entries = Object.entries(value)
      .filter(([, item]) => item !== undefined && item !== null && item !== "")
      .filter(([, item]) => !(Array.isArray(item) && item.length === 0))
      .map(([key, item]) => [key, compact(item)]);
    return Object.fromEntries(entries);
  }
  return value;
}

function weeklyRows(source, parent, owner, intervals) {
  const groups = new Map();
  for (const { day, opens, closes } of intervals) {
    const key = `${opens}-${closes}`;
    groups.set(key, { opens, closes, days: [...(groups.get(key)?.days ?? []), DAYS[day]] });
  }
  return [...groups.values()].map((group, index) => ({
    id: uuid(source, "schedule", `${parent}:${index}`),
    ...owner,
    freq: "WEEKLY",
    byday: group.days.join(","),
    opens_at: `${group.opens}${OFFSET}`,
    closes_at: `${group.closes}${OFFSET}`,
    x_resource_engine: { timezone: "America/Los_Angeles" },
  }));
}

function fromNdp(organization, service) {
  const id = uuid("ndp", "service", service.id);
  const orgId = uuid("ndp", "organization", organization.id);
  return compact({
    id,
    organization_id: orgId,
    name: service.name,
    status: "active",
    description: stripHtml(service.description),
    url: service.url,
    email: service.email,
    application_process: service.applicationProcess,
    fees_description: service.fees?.description,
    eligibility_description: service.eligibility?.description,
    assured_date: service.meta?.lastVerified,
    organization: {
      id: orgId,
      name: organization.name,
      description: stripHtml(organization.description) ?? `No description available for ${organization.name}.`,
      website: organization.url,
    },
    phones: (service.phones ?? []).map((phone) => ({
      id: uuid("ndp", "phone", phone.id),
      service_id: id,
      number: phone.number,
      type: phone.type === "fax" ? "fax" : "voice",
      description: phone.description,
    })),
    schedules: (service.schedules ?? []).flatMap((schedule) =>
      weeklyRows(
        "ndp",
        schedule.id,
        { service_id: id },
        (schedule.open ?? []).map((row) => ({ day: row.day, opens: row.opensAt, closes: row.closesAt })),
      ),
    ),
    service_areas: (service.serviceAreas ?? []).map((area) => ({
      id: uuid("ndp", "service_area", area.id),
      service_id: id,
      name: area.value,
    })),
    x_resource_engine: {
      source: { system: "ndp", record_id: service.id, fetched_at: "2026-11-03T15:00:00Z" },
    },
  });
}

function fromOdrs(opportunity) {
  const id = uuid("odrs", "service", opportunity.id);
  const orgId = uuid("odrs", "organization", opportunity.organization.id);
  const properties = opportunity.properties ?? {};
  const counties = Object.keys(properties)
    .filter((key) => key.startsWith("service-county-california-"))
    .map((key) =>
      key
        .replace("service-county-california-", "")
        .split("-")
        .map((word) => word[0].toUpperCase() + word.slice(1))
        .join(" "),
    );
  const location = opportunity.locations?.[0];
  const hours = location?.schedule ?? {};
  const days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"];
  const intervals = days
    .filter((day) => hours[`${day}_start`] && hours[`${day}_end`])
    .map((day) => ({ day: day.slice(0, 3), opens: hours[`${day}_start`], closes: hours[`${day}_end`] }));
  return compact({
    id,
    organization_id: orgId,
    name: opportunity.title,
    status: "active",
    description: opportunity.description,
    url: properties["action-signup-url"],
    email: opportunity.emails?.[0]?.email,
    fees_description: properties["cost-free"] ? "Free" : undefined,
    eligibility_description: properties["elig-description"],
    assured_date: opportunity.last_verified_at?.slice(0, 10),
    organization: {
      id: orgId,
      name: opportunity.organization.name,
      description: `No description available for ${opportunity.organization.name}.`,
      website: opportunity.organization.website,
    },
    phones: (opportunity.phones ?? []).map((phone) => ({
      id: uuid("odrs", "phone", phone.id),
      service_id: id,
      number: phone.digits,
      type: "voice",
      description: phone.phone_type,
    })),
    schedules: weeklyRows("odrs", opportunity.id, { service_id: id }, intervals),
    service_areas: counties.map((name) => ({
      id: uuid("odrs", "service_area", `${opportunity.id}:${name}`),
      service_id: id,
      name: `${name} County`,
    })),
    x_resource_engine: {
      source: { system: "odrs", record_id: String(opportunity.id), fetched_at: "2026-11-03T15:00:00Z" },
    },
  });
}

function fictional(native, name, organization, eligibility) {
  const id = uuid("ndp", "service", native);
  const orgId = uuid("ndp", "organization", native);
  return {
    id,
    organization_id: orgId,
    name,
    status: "active",
    description: `${name}, offered by ${organization}.`,
    eligibility_description: eligibility,
    assured_date: "2025-03-14",
    organization: {
      id: orgId,
      name: organization,
      description: `${organization} is a fictional organization for this demo.`,
      website: `https://${native}.example.org`,
    },
    phones: [{ id: uuid("ndp", "phone", native), service_id: id, number: "(415) 555-0142", type: "voice" }],
    x_resource_engine: {
      source: { system: "ndp", record_id: native, fetched_at: "2026-11-03T15:00:00Z" },
    },
  };
}

function finding(key, service, category, fields) {
  return {
    id: uuid("demo", "finding", key),
    service_id: service.id,
    ref: { entity_type: "service", entity_id: service.id, field: fields.field, op: fields.op ?? "set" },
    category,
    current_value: fields.current ?? null,
    proposed_value: fields.proposed ?? null,
    evidence: fields.evidence,
    confidence: fields.confidence,
    model_id: fields.model ?? null,
    created_at: CHECKED_AT,
    review_state: "pending",
  };
}

const quote = (url, snippet) => ({
  kind: "quote",
  source_type: "website",
  url,
  fetched_at: CHECKED_AT,
  snippet,
});
const signal = (url, details) => ({
  kind: "http_signal",
  source_type: "website",
  url,
  fetched_at: CHECKED_AT,
  ...details,
});

// Sample records.
const ndpOrganizations = [1, 2].flatMap(
  (page) => read(`data/samples/NDP/national-data-platform-sample-${page}.json`).data,
);
const ndpRecords = ndpOrganizations
  .flatMap((organization) =>
    organization.services
      .filter((service) => service.meta?.status === "active" && service.meta?.access === "public")
      .filter((service) => service.id !== "211bayarea-10613258")
      .map((service) => fromNdp(organization, service)),
  )
  .slice(0, 16);
const odrsRecords = read("data/samples/NDP/BayesExport_20250729.json")
  .opportunities.filter((opportunity) => opportunity.id !== 6126)
  .slice(0, 12)
  .map(fromOdrs);

// Fixtures.
const coastsideNdp = read("contracts/fixtures/coastside_hope.ndp.hsds.json");
const coastsideOdrs = read("contracts/fixtures/coastside_hope.odrs.hsds.json");
const fixtureFindings = read("contracts/fixtures/findings.json");
const fixtureClashes = read("contracts/fixtures/clashes.json");

// Seed set, nested the way the API returns it.
const seed = read("seed/hsds_dataset.json");
const manifest = read("seed/website_content/manifest.json").entries;
const organizationsById = new Map(seed.organizations.map((item) => [item.id, item]));
const seedRecords = seed.services.map((service) => {
  const { x_resource_engine: _omit, url: website, ...organization } =
    organizationsById.get(service.organization_id);
  const source = service.x_resource_engine.source;
  const system = source.system.startsWith("ndp") ? "ndp" : source.system;
  return {
    ...service,
    organization: { ...organization, website },
    x_resource_engine: { ...service.x_resource_engine, source: { ...source, system } },
  };
});
const bySeedCondition = (condition) => {
  const entry = manifest.find((item) => item.seeded_condition === condition);
  return { entry, record: seedRecords.find((item) => item.id === entry.service_id) };
};

// Fictional organizations, so the queue shows every category.
const northgate = fictional(
  "northgate-legal",
  "Tenant Legal Clinic",
  "Northgate Legal Aid",
  "Open to tenants in Alameda County with an income at or below 200% of the federal poverty level.",
);
const riverside = fictional(
  "riverside-clinic",
  "Walk-in Primary Care",
  "Riverside Family Clinic",
  "Open to all adults.",
);
const mission = fictional(
  "mission-youth",
  "After-school Tutoring",
  "Mission Youth Center",
  "Students in grades 6 to 12.",
);

const discrepancySeed = bySeedCondition("discrepancy");
const verifiedSeed = bySeedCondition("verified");
const closedSeed = bySeedCondition("actively_bad");

const findings = [
  ...fixtureFindings,
  finding("bayview", discrepancySeed.record, "discrepancy", {
    field: discrepancySeed.entry.field,
    current: discrepancySeed.entry.stored_value,
    proposed: discrepancySeed.entry.truth,
    evidence: [quote(discrepancySeed.entry.url, discrepancySeed.entry.truth)],
    confidence: 0.94,
    model: "sample-model",
  }),
  finding("harbor", closedSeed.record, "actively_bad", {
    field: "status",
    current: "active",
    evidence: [signal(closedSeed.entry.url, { error: "DNS lookup failed" })],
    confidence: 0.7,
  }),
  finding("northgate-income", northgate, "discrepancy", {
    field: "eligibility_description",
    current: northgate.eligibility_description,
    proposed: "Open to tenants in Alameda County with an income at or below 300% of the federal poverty level.",
    evidence: [
      quote(
        "https://northgate-legal.example.org/eligibility",
        "Tenants in Alameda County qualify with an income at or below 300% of the federal poverty level.",
      ),
    ],
    confidence: 0.88,
    model: "sample-model",
  }),
  finding("riverside-timeout", riverside, "check_failed", {
    field: "status",
    current: "active",
    evidence: [signal("https://riverside-clinic.example.org", { error: "Request timed out after 30 seconds" })],
    confidence: 0.2,
  }),
  finding("mission-cert", mission, "actively_bad", {
    field: "status",
    current: "active",
    evidence: [signal("https://mission-youth.example.org", { error: "TLS certificate expired" })],
    confidence: 0.65,
  }),
];

const records = [
  coastsideNdp,
  coastsideOdrs,
  ...seedRecords,
  northgate,
  riverside,
  mission,
  ...ndpRecords,
  ...odrsRecords,
];

// Checks: simulated findings first, then one verified seed, then the real gaps in the samples.
const checks = {};
for (const item of findings) {
  (checks[item.service_id] ??= {
    service_id: item.service_id,
    category: item.category,
    checked_at: CHECKED_AT,
    finding_ids: [],
  }).finding_ids.push(item.id);
}
checks[verifiedSeed.record.id] = {
  service_id: verifiedSeed.record.id,
  category: "verified",
  checked_at: CHECKED_AT,
  finding_ids: [],
};
const hasGap = (record) =>
  !record.eligibility_description || !record.schedules?.length || !record.phones?.length;
for (const record of [...ndpRecords, ...odrsRecords]) {
  if (hasGap(record)) {
    checks[record.id] = {
      service_id: record.id,
      category: "missing_information",
      checked_at: CHECKED_AT,
      finding_ids: [],
    };
  }
}

const areaNames = (record) => (record.service_areas ?? []).map((area) => area.name);
const scheduleText = (record) => {
  const rows =
    record.schedules ??
    record.service_at_locations?.flatMap((link) => link.location?.schedules ?? []) ??
    [];
  const names = { MO: "Mon", TU: "Tue", WE: "Wed", TH: "Thu", FR: "Fri", SA: "Sat", SU: "Sun" };
  const days = (byday) => byday.split(",").map((day) => names[day]).join(" ");
  const time = (value) => value.slice(0, 5);
  return rows.map((row) => `${days(row.byday)} ${time(row.opens_at)} to ${time(row.closes_at)}`);
};
const comparisons = {
  [coastsideNdp.id]: [
    {
      field: "Service area",
      agree: false,
      values: [
        { source: "ndp", value: areaNames(coastsideNdp).join(", "), observed_at: coastsideNdp.assured_date },
        { source: "odrs", value: areaNames(coastsideOdrs).join(", "), observed_at: coastsideOdrs.assured_date },
      ],
    },
    {
      field: "Hours",
      agree: false,
      note: "ODRS splits the day around a lunch closure.",
      values: [
        { source: "ndp", value: scheduleText(coastsideNdp).join("; "), observed_at: coastsideNdp.assured_date },
        { source: "odrs", value: scheduleText(coastsideOdrs).join("; "), observed_at: coastsideOdrs.assured_date },
      ],
    },
    {
      field: "Email",
      agree: false,
      values: [
        { source: "ndp", value: coastsideNdp.email, observed_at: coastsideNdp.assured_date },
        { source: "odrs", value: coastsideOdrs.email, observed_at: coastsideOdrs.assured_date },
      ],
    },
    {
      field: "Fees",
      agree: true,
      note: "Different wording, same fact.",
      values: [
        { source: "ndp", value: coastsideNdp.fees_description, observed_at: coastsideNdp.assured_date },
        { source: "odrs", value: coastsideOdrs.fees_description, observed_at: coastsideOdrs.assured_date },
      ],
    },
  ],
};

const count = (source) => records.filter((item) => item.x_resource_engine.source.system === source).length;
const output = {
  records: records.map((service) => ({ service, version: 1 })),
  findings,
  clashes: fixtureClashes,
  checks,
  comparisons,
  ingest: {
    ndp: { received: count("ndp") + 1, stored: count("ndp"), quarantined: 1 },
    odrs: { received: count("odrs"), stored: count("odrs"), quarantined: 0 },
  },
};
writeFileSync(resolve(here, "../src/api/mock/demo-data.json"), `${JSON.stringify(output, null, 2)}\n`);
console.log(`${records.length} records, ${findings.length} findings, ${Object.keys(checks).length} checks`);
