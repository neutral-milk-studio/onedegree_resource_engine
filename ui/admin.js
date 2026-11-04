// One Degree admin console: overview, review queue, records, providers, seekers, imports, documents, API keys.
import { h, api, toast, modal, closeModal, badge, errBox, fmtDate, fmtDay, empty, table, params, qs, pager, diff, LABEL } from "./ui.js";
import { recordEditor } from "./records.js";

const CATS = ["actively_bad", "check_failed", "discrepancy", "missing_information", "verified", "unchecked"];

export function admin(page, need) {
  const guard = (fn) => async (arg) => { if (!need("admin")) return; return fn(arg); };
  const app = () => document.getElementById("app");
  const show = (...k) => app().replaceChildren(page(...k));
  const refresh = () => window.dispatchEvent(new Event("hashchange"));

  async function overview() {
    const s = await api("/admin/stats");
    const total = Object.values(s.check_categories).reduce((a, b) => a + b, 0) || 1;
    const pend = Object.values(s.pending_by_kind).reduce((a, b) => a + b, 0);
    const act = (label, kind, body) => h("button", { class: "ghost", onclick: async (e) => {
      e.target.disabled = true; try { await api(kind, { method: "POST", body: { ...body, wait: true } }); toast(`${label} finished.`); refresh(); } catch (x) { toast(x.message); e.target.disabled = false; } } }, label);
    show(h("div", { class: "row spread" }, h("h1", null, "Overview"),
      h("div", { class: "row" }, act("Import all sources", "/admin/imports/run", {}), act("Check due records", "/admin/maintenance/run", {}), act("Rebuild search index", "/admin/reindex", {}))),
      h("div", { class: "grid cols-3" },
        h("a", { class: "card stat", href: "#/admin/review" }, h("b", null, pend), "changes waiting for review", h("div", { class: "muted small" }, Object.entries(s.pending_by_kind).map(([k, v]) => `${v} ${k.replace("_", " ")}`).join(" · "))),
        h("a", { class: "card stat", href: "#/admin/records" }, h("b", null, s.counts.service || 0), "services", h("div", { class: "muted small" }, `${s.counts.organization || 0} organizations · ${s.counts.location || 0} locations`)),
        h("a", { class: "card stat", href: "#/admin/seekers" }, h("b", null, s.open_requests), "open help requests", h("div", { class: "muted small" }, `${s.seekers} help seekers · ${s.providers} provider accounts`)),
        h("a", { class: "card stat", href: "#/admin/imports" }, h("b", null, s.quarantined), "quarantined source records", h("div", { class: "muted small" }, "could not be converted to valid HSDS")),
        h("a", { class: "card stat", href: "#/admin/documents?status=pending" }, h("b", null, s.docs_pending), "documents waiting for approval")),
      h("h2", { style: "margin-top:1.5rem" }, "Record health"),
      h("div", { class: "card" }, h("div", { class: "bars" }, CATS.map((c) => h("a", { class: "bar-row", href: `#/admin/records${qs({ category: c })}` }, badge(c),
        h("div", { class: "bar-track" }, h("div", { class: "bar-fill", style: `width:${((s.check_categories[c] || 0) / total) * 100}%` }), ), h("span", null, s.check_categories[c] || 0))))),
      h("h2", { style: "margin-top:1.5rem" }, "Latest imports"), importRuns(s.last_imports),
      h("h2", { style: "margin-top:1.5rem" }, "Latest maintenance runs"),
      table(["Started", "Scope", "Trigger", "Status", "Result"], s.last_maintenance.map((r) => [fmtDate(r.started_at), r.scope, `${r.trigger} · ${r.initiated_by}`, badge(r.status), countsLine(JSON.parse(r.counts || "{}"))]), "No maintenance runs yet."));
  }
  const countsLine = (c) => ["actively_bad", "check_failed", "discrepancy", "missing_information", "verified"].filter((k) => c[k]).map((k) => `${c[k]} ${LABEL[k].toLowerCase()}`).join(", ") || "—";
  const importRuns = (runs) => table(["Started", "Source", "Received", "Stored", "Quarantined", "Clashes", "Balanced", "Status"],
    runs.map((r) => [fmtDate(r.started_at), r.source, r.received, r.stored, r.quarantined, r.clashes, r.status === "running" ? "…" : r.received === r.stored + r.quarantined ? "Yes" : "No", badge(r.status)]), "No imports yet.");

  async function review() {
    const p = params(), kind = p.kind || "", status = p.status || "pending";
    const { change_requests: crs } = await api(`/admin/change-requests${qs({ status, kind })}`);
    const decide = async (cr, action, force) => {
      try { await api(`/admin/change-requests/${cr.id}/${action}`, { method: "POST", body: { force } }); toast(action === "approve" ? "Applied. Change history updated." : "Rejected."); refresh(); }
      catch (e) {
        if (e.status === 409 && e.data?.stale_fields) modal("This record changed since the proposal", h("p", null, `Someone edited ${e.data.stale_fields.join(", ")} after this was proposed. Apply anyway?`),
          [h("button", { onclick: () => { closeModal(); decide(cr, "approve", true); } }, "Apply anyway")]);
        else modal("Could not apply", errBox(e));
      }
    };
    const kinds = [["", "All"], ["ai_finding", "AI findings"], ["provider_update", "Provider updates"], ["conflict", "Source clashes"]];
    show(h("h1", null, "Review"), h("p", { class: "muted" }, "Nothing here changes a live record until you approve it. Each approval records who proposed it, who approved it and when."),
      h("div", { class: "row" }, kinds.map(([k, l]) => h("a", { class: `chip${k === kind ? "" : ""}`, style: k === kind ? "border-color:var(--tide);background:var(--tide-l)" : "", href: `#/admin/review${qs({ kind: k, status })}` }, l)),
        h("select", { style: "width:auto", "aria-label": "Status", onchange: (e) => (location.hash = `#/admin/review${qs({ kind, status: e.target.value })}`) }, ["pending", "applied", "rejected"].map((s) => h("option", { value: s, selected: s === status ? true : null }, LABEL[s])))),
      crs.length ? crs.map((cr) => h("div", { class: "card", style: "margin-top:1rem" },
        h("div", { class: "row spread" }, h("div", null, h("strong", null, cr.title || cr.kind), h("div", { class: "muted small" }, `${cr.resource_name} · ${cr.org_name || ""} · from ${cr.proposed_by_type === "ai" ? "AI maintenance" : cr.proposed_by} · ${fmtDate(cr.created_at)}`)),
          h("div", { class: "row" }, cr.category && badge(cr.category), badge(cr.status), h("a", { class: "small", href: `#/admin/record/${cr.resource_id}` }, "Open record"))),
        cr.stale_fields?.length ? h("div", { class: "err" }, `The record changed since this was proposed (${cr.stale_fields.join(", ")}).`) : null,
        ...Object.entries(cr.patch).map(([f, v]) => diff(f, v.current, v.proposed)),
        ...(cr.evidence || []).map((e) => h("blockquote", null, `“${e.snippet}”`, h("div", { class: "small muted" }, h("a", { href: e.url, target: "_blank", rel: "noopener" }, e.url), ` · found by ${e.model}`))),
        cr.status === "pending" ? h("div", { class: "row", style: "margin-top:.6rem" }, h("button", { onclick: () => decide(cr, "approve") }, cr.kind === "conflict" ? "Use the source value" : "Approve and apply"),
          h("button", { class: "danger", onclick: () => decide(cr, "reject") }, cr.kind === "conflict" ? "Keep the current value" : "Reject")) : h("div", { class: "small muted" }, `Decided by ${cr.decided_by} on ${fmtDate(cr.decided_at)}`)))
        : empty("Nothing is waiting for review. New AI findings and provider updates will appear here."));
  }

  async function records() {
    const p = params(), pg = Number(p.page || 1);
    const d = await api(`/admin/maintenance/results${qs({ category: p.category, q: p.q, source: p.source, page: pg, per_page: 25 })}`);
    const q = h("input", { value: p.q || "", placeholder: "Search by name…", "aria-label": "Search records", style: "max-width:260px" });
    const go = (o) => (location.hash = `#/admin/records${qs({ ...p, ...o })}`);
    show(h("div", { class: "row spread" }, h("h1", null, "Records"), h("button", { class: "ghost", onclick: async (e) => { e.target.disabled = true; await api("/admin/maintenance/run", { method: "POST", body: { wait: true } }); toast("Check finished."); refresh(); } }, "Check due records now")),
      h("div", { class: "row" }, h("select", { style: "width:auto", "aria-label": "Category", onchange: (e) => go({ category: e.target.value, page: 1 }) },
        h("option", { value: "" }, "All categories"), CATS.map((c) => h("option", { value: c, selected: p.category === c ? true : null }, LABEL[c]))),
        h("select", { style: "width:auto", "aria-label": "Source", onchange: (e) => go({ source: e.target.value, page: 1 }) }, h("option", { value: "" }, "All sources"), ["odrs", "ndp"].map((s) => h("option", { value: s, selected: p.source === s ? true : null }, s))),
        q, h("button", { class: "ghost", onclick: () => go({ q: q.value, page: 1 }) }, "Search")),
      h("div", { style: "margin-top:.75rem" }, table(["Service", "Organization", "Source", "Result", "Checked", "Waiting", ""], d.results.map((r) => [
        h("span", null, r.verified ? "✓ " : "", r.name), r.organization || "—", r.source, h("div", null, badge(r.category), r.summary && h("div", { class: "muted small" }, r.summary)),
        fmtDate(r.checked_at), r.pending || "—", h("div", { class: "row" }, h("a", { class: "btn ghost sm", href: `#/admin/record/${r.id}` }, "Open"),
          h("button", { class: "ghost sm", onclick: async (e) => { e.target.disabled = true; await api("/admin/maintenance/run", { method: "POST", body: { service_id: r.id, wait: true } }); toast("Checked."); refresh(); } }, "Check now"))]),
        "No records match these filters.")), pager(d.total, pg, 25, (n) => go({ page: n })));
  }

  async function record(id) {
    const body = await recordEditor(id, { mode: "admin", onSaved: refresh });
    const r = await api(`/admin/records/${id}`);
    show(h("a", { href: "#/admin/records" }, "← Records"), h("h1", null, r.record.data.name || r.record.type), h("p", { class: "muted small" }, `${r.record.type} · source ${r.record.source} · version ${r.record.version} · updated ${fmtDate(r.record.updated_at)}`),
      h("div", { class: "card" }, body));
  }

  async function providers() {
    const p = params(), pg = Number(p.page || 1);
    const d = await api(`/admin/providers${qs({ q: p.q, page: pg, per_page: 25 })}`);
    const q = h("input", { value: p.q || "", placeholder: "Search organizations…", style: "max-width:280px", "aria-label": "Search organizations" });
    show(h("h1", null, "Providers"), h("div", { class: "row" }, q, h("button", { class: "ghost", onclick: () => (location.hash = `#/admin/providers${qs({ q: q.value })}`) }, "Search")),
      h("div", { style: "margin-top:.75rem" }, table(["Organization", "Source", "Services", "Record health", "Waiting", ""], d.organizations.map((o) => [
        h("span", null, o.name, o.flags.includes("description_missing") && h("div", { class: "muted small" }, "Description missing from source")), o.source, o.services,
        h("div", { class: "row" }, Object.entries(o.checks).map(([k, n]) => h("span", null, badge(k), ` ${n}`))), o.pending || "—", h("a", { class: "btn ghost sm", href: `#/admin/provider/${o.id}` }, "Open")]))),
      pager(d.total, pg, 25, (n) => (location.hash = `#/admin/providers${qs({ q: p.q, page: n })}`)));
  }
  async function provider(id) {
    const d = await api(`/admin/providers/${id}`);
    const email = h("input", { type: "email" }), name = h("input"), pw = h("input", { type: "password" }), out = h("div");
    show(h("a", { href: "#/admin/providers" }, "← Providers"), h("div", { class: "row spread" }, h("div", null, h("h1", null, d.organization.data.name), h("p", { class: "muted" }, d.organization.data.description)),
      h("button", { onclick: async () => modal("Edit organization", await recordEditor(id, { mode: "admin", onSaved: () => { closeModal(); refresh(); } })) }, "Edit organization")),
      h("h2", null, "Services"), table(["Service", "Status", "Result", "Waiting", ""], d.services.map((s) => [s.data.name, s.data.status, badge(s.check ? s.check.category : "unchecked"), s.pending || "—", h("a", { class: "btn ghost sm", href: `#/admin/record/${s.id}` }, "Open")])),
      h("h2", { style: "margin-top:1.5rem" }, "Locations"), table(["Location", "Address", ""], d.locations.map((l) => [l.data.name || "Location", (l.data.addresses || []).map((a) => `${a.address_1}, ${a.city}`).join("; ") || "—", h("a", { class: "btn ghost sm", href: `#/admin/record/${l.id}` }, "Open")])),
      h("h2", { style: "margin-top:1.5rem" }, "Provider logins"), table(["Name", "Email"], d.users.map((u) => [u.name, u.email]), "No provider has a login for this organization yet."),
      h("div", { class: "card flat", style: "max-width:460px;margin-top:.75rem" }, h("strong", null, "Create a provider login"), out, h("label", null, "Name"), name, h("label", null, "Email"), email, h("label", null, "Temporary password"), pw,
        h("div", { style: "margin-top:.8rem" }, h("button", { onclick: async () => { try { await api("/admin/users", { method: "POST", body: { role: "provider", org_id: id, name: name.value, email: email.value, password: pw.value } }); toast("Login created."); refresh(); } catch (e) { out.replaceChildren(errBox(e)); } } }, "Create login"))),
      h("h2", { style: "margin-top:1.5rem" }, "Recent change requests"),
      table(["When", "What", "From", "Status"], d.change_requests.map((c) => [fmtDate(c.created_at), c.title || c.kind, c.proposed_by, badge(c.status)]), "No change requests."));
  }

  async function seekers() {
    const { seekers } = await api("/admin/seekers");
    show(h("h1", null, "Help seekers"), table(["Name", "Email", "ZIP", "Shares with providers", "Requests", "Joined", ""], seekers.map((s) => [s.name, s.email, s.zip || "—", s.shares ? "Yes" : "No", s.requests, fmtDay(s.created_at), h("a", { class: "btn ghost sm", href: `#/admin/seeker/${s.id}` }, "Open")]), "Nobody has created a profile yet."));
  }
  async function seeker(id) {
    const d = await api(`/admin/seekers/${id}`);
    show(h("a", { href: "#/admin/seekers" }, "← Help seekers"), h("h1", null, d.user.name), h("p", { class: "muted" }, d.user.email),
      h("div", { class: "card", style: "max-width:560px" }, h("h2", null, "Profile"), Object.entries(d.profile).map(([k, v]) => h("div", { class: "fieldrow" }, h("strong", null, k.replaceAll("_", " ")), String(v)))),
      h("h2", { style: "margin-top:1.5rem" }, "Requests"), table(["Sent", "Need", "Provider", "Status"], d.requests.map((r) => [fmtDate(r.created_at), r.need_text, r.org_name || "Unassigned", badge(r.status)]), "No requests."));
  }

  async function imports() {
    const d = await api("/admin/imports"), q = await api("/admin/quarantine");
    const run = (source) => async (e) => { e.target.disabled = true; try { await api("/admin/imports/run", { method: "POST", body: { source, wait: true } }); toast("Import finished."); } catch (x) { toast(x.message); } refresh(); };
    show(h("div", { class: "row spread" }, h("h1", null, "Imports"), h("button", { onclick: run() }, "Import all sources now")),
      h("h2", null, "Sources"), table(["Name", "Adapter", "Location", ""], d.sources.map((s) => [s.name, s.adapter, h("span", { class: "mono" }, s.location), h("button", { class: "ghost sm", onclick: run(s.name) }, "Import now")])),
      h("h2", { style: "margin-top:1.5rem" }, "Runs"), h("p", { class: "muted small" }, "Every run must balance: received = stored + quarantined."), importRuns(d.runs),
      h("h2", { style: "margin-top:1.5rem" }, "Quarantine"), table(["When", "Source", "Record", "Why it was held back"], q.quarantine.map((r) => [fmtDate(r.created_at), r.source, r.native_id, r.reason]), "Nothing is quarantined."));
  }

  async function documents() {
    const p = params(), { documents } = await api(`/admin/documents${qs({ status: p.status })}`);
    const decide = (id, approve) => async () => { try { await api(`/admin/documents/${id}/decide`, { method: "POST", body: { approve } }); toast(approve ? "Approved." : "Rejected."); refresh(); } catch (e) { toast(e.message); } };
    const file = h("input", { type: "file", accept: ".pdf,.docx,.txt,.md,.html" }), title = h("input"), org = h("input", { placeholder: "Organization id (optional)" });
    show(h("h1", null, "Policy documents"), h("div", { class: "row" }, ["", "pending", "approved", "rejected"].map((s) => h("a", { class: "chip", href: `#/admin/documents${qs({ status: s })}`, style: (p.status || "") === s ? "border-color:var(--tide);background:var(--tide-l)" : "" }, s ? LABEL[s] : "All"))),
      h("div", { style: "margin-top:.75rem" }, table(["Title", "Organization", "File", "Uploaded by", "Chunks", "Status", ""], documents.map((d) => [d.title, d.org_id ? d.org_id.slice(0, 8) : "General", d.filename, d.uploaded_by, d.chunk_count, badge(d.status),
        d.status === "pending" ? h("div", { class: "row" }, h("button", { class: "sm", onclick: decide(d.id, true) }, "Approve"), h("button", { class: "sm danger", onclick: decide(d.id, false) }, "Reject")) : d.extraction !== "ok" ? h("span", { class: "muted small" }, d.extraction) : ""]), "No documents.")),
      h("div", { class: "card", style: "max-width:520px;margin-top:1.2rem" }, h("h2", null, "Upload a document"), h("label", null, "File"), file, h("label", null, "Title"), title, h("label", null, "Organization id"), org,
        h("div", { style: "margin-top:.8rem" }, h("button", { onclick: async () => { const fd = new FormData(); if (!file.files[0]) return toast("Choose a file first."); fd.append("file", file.files[0]); fd.append("title", title.value || file.files[0].name); fd.append("org_id", org.value);
          try { await api("/admin/documents", { method: "POST", form: fd }); toast("Uploaded and approved."); refresh(); } catch (e) { toast(e.message); } } }, "Upload and approve"))));
  }

  async function keys() {
    const [{ keys }, imp] = await Promise.all([api("/admin/api-keys"), api("/admin/imports")]);
    const name = h("input", { placeholder: "e.g. 1degree.org front end" }), scope = h("select", null, h("option", { value: "" }, "All sources"), imp.sources.map((s) => h("option", { value: s.name }, `Only ${s.name}`)));
    const out = h("div");
    show(h("h1", null, "API keys"), h("p", { class: "muted" }, "The HSDS API is not open. Each caller needs a key, sent as a bearer token. A key can be limited to one source."),
      h("div", { class: "card", style: "max-width:520px" }, out, h("label", null, "Name"), name, h("label", null, "Access"), scope,
        h("div", { style: "margin-top:.8rem" }, h("button", { onclick: async () => { try { const r = await api("/admin/api-keys", { method: "POST", body: { name: name.value, source_scope: scope.value } });
          out.replaceChildren(h("div", { class: "ok" }, h("strong", null, "Copy this key now. It is not shown again."), h("div", { class: "mono", style: "word-break:break-all;margin-top:.3rem" }, r.key),
            h("div", { class: "mono small", style: "margin-top:.5rem;word-break:break-all" }, `curl -H "Authorization: Bearer ${r.key}" ${location.origin}/hsds/services?per_page=5`))); } catch (e) { out.replaceChildren(errBox(e)); } } }, "Create key"))),
      h("div", { style: "margin-top:1.2rem" }, table(["Name", "Key", "Access", "Created", "Last used", ""], keys.map((k) => [k.name, h("span", { class: "mono" }, `${k.key_prefix}…`), k.source_scope || "All sources", fmtDate(k.created_at), fmtDate(k.last_used_at),
        k.revoked_at ? badge("rejected") : h("button", { class: "sm danger", onclick: async () => { await api(`/admin/api-keys/${k.id}`, { method: "DELETE" }); toast("Key revoked."); refresh(); } }, "Revoke")]), "No keys yet.")));
  }

  return {
    "/admin": guard(overview), "/admin/review": guard(review), "/admin/records": guard(records), "/admin/record/:id": guard(record),
    "/admin/providers": guard(providers), "/admin/provider/:id": guard(provider), "/admin/seekers": guard(seekers), "/admin/seeker/:id": guard(seeker),
    "/admin/imports": guard(imports), "/admin/documents": guard(documents), "/admin/keys": guard(keys),
  };
}
