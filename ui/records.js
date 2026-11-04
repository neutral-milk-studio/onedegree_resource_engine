// Spreadsheet-like record editor shared by providers (propose) and admins (save directly).
import { h, api, toast, errBox, badge, fmtDate, summarize, diff, table, state } from "./ui.js";

const HIDDEN = new Set(["id", "x_resource_engine"]);
const isComplex = (v) => v !== null && typeof v === "object";

export async function recordEditor(rid, { mode, onSaved }) {
  const base = mode === "admin" ? `/admin/records/${rid}` : `/provider/records/${rid}`;
  const info = await api(base);
  const rec = info.record, data = rec.data;
  const root = h("div");
  const errs = h("div");
  const rows = new Map();
  const fields = Object.keys(data).filter((k) => !HIDDEN.has(k)).sort();

  function fieldRow(name, value, isNew = false) {
    const complex = isComplex(value);
    const text = complex ? JSON.stringify(value, null, 1) : value ?? "";
    const input = complex || String(text).length > 90
      ? h("textarea", { class: complex ? "json" : "", "aria-label": name }, text)
      : h("input", { value: text, "aria-label": name, type: typeof value === "number" ? "number" : "text" });
    const row = h("div", { class: "fieldrow" }, h("label", { style: "margin:0" }, name.replaceAll("_", " ")), input);
    input.addEventListener("input", () => row.classList.toggle("dirty", input.value !== String(text)));
    rows.set(name, { input, complex, original: value, row, typeNum: typeof value === "number", isNew });
    return row;
  }
  const grid = h("div", null, fields.map((f) => fieldRow(f, data[f])));
  const newName = h("input", { placeholder: "Add a field, e.g. wait_time", "aria-label": "New field name" });
  const addBtn = h("button", { class: "ghost sm", onclick: () => {
    const n = newName.value.trim(); if (!n || rows.has(n)) return;
    const r = fieldRow(n, "", true); r.classList.add("dirty"); grid.append(r); newName.value = "";
  } }, "Add field");

  function collect() {
    const patch = {};
    for (const [name, r] of rows) {
      let v = r.input.value;
      if (r.complex) { try { v = JSON.parse(v); } catch { throw new Error(`"${name}" is not valid JSON.`); } }
      else if (r.typeNum && v !== "") v = Number(v);
      else if (!r.complex && r.original !== undefined && typeof r.original === "string" && v === r.original) continue;
      if (JSON.stringify(v) === JSON.stringify(r.original) || (r.isNew && v === "")) continue;
      patch[name] = v === "" ? null : v;
    }
    return patch;
  }
  const save = h("button", { onclick: async () => {
    errs.replaceChildren();
    try {
      const patch = collect();
      if (!Object.keys(patch).length) return toast("No changes to save.");
      if (mode === "admin") { await api(base, { method: "PUT", body: { patch, expected_version: rec.version } }); toast("Saved. Change history updated."); }
      else { await api("/provider/change-requests", { method: "POST", body: { resource_id: rid, patch } }); toast("Sent for One Degree review."); }
      onSaved && onSaved();
    } catch (e) { errs.replaceChildren(e.status ? errBox(e) : h("div", { class: "err" }, e.message)); }
  } }, mode === "admin" ? "Save changes" : "Send for review");

  const check = info.check;
  root.append(
    h("p", { class: "muted small" }, mode === "admin"
      ? "Edits save immediately and are checked against the HSDS schema first."
      : "Your edits go to One Degree for review. They go live after approval."),
    check ? h("div", { class: "row", style: "margin:.4rem 0" }, badge(check.category), h("span", { class: "small muted" }, `${check.summary || ""} Checked ${fmtDate(check.checked_at)}.`)) : null,
    errs, grid,
    h("div", { class: "row", style: "margin-top:.6rem" }, newName, addBtn),
    h("div", { class: "row", style: "margin-top:1rem" }, save),
  );
  const pend = (info.change_requests || []).filter((c) => c.status === "pending");
  if (pend.length) root.append(h("h3", { style: "margin-top:1.4rem" }, "Waiting for review"),
    ...pend.map((c) => h("div", { class: "card flat", style: "margin:.4rem 0" }, h("strong", null, c.title || c.kind),
      ...Object.entries(c.patch).map(([f, v]) => diff(f, v.current, v.proposed)))));
  root.append(h("h3", { style: "margin-top:1.4rem" }, "Change history"),
    table(["When", "Field", "From", "To", "By"], info.history.slice(0, 25).map((x) => [fmtDate(x.last_action_date), x.field_name,
      summarize(x.previous_value && safeParse(x.previous_value)), summarize(x.replacement_value && safeParse(x.replacement_value)),
      h("span", null, x.updated_by, h("br"), h("span", { class: "muted small" }, x.actor_type + (x.note ? ` · ${x.note}` : "")))]), "No changes recorded yet."));
  return root;
}
const safeParse = (s) => { try { return JSON.parse(s); } catch { return s; } };
