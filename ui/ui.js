// Small UI toolkit: DOM builder (no innerHTML with data), API client, toast, modal, shared widgets.
export const state = { user: null, token: localStorage.getItem("re_token") || null };

// Links can come from source data, so only web and in-app links are allowed.
export function safeHref(url) {
  const value = String(url).trim();
  return /^(https?:\/\/|#|\/)/i.test(value) ? value : "#";
}

export function h(tag, props, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (k === "value") el.value = v;
    else if (k === "href") el.setAttribute(k, safeHref(v));
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) {
    if (kid == null || kid === false) continue;
    el.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
  }
  return el;
}
export const $ = (s, r = document) => r.querySelector(s);

export async function api(path, { method = "GET", body, form } = {}) {
  const headers = {};
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { headers["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(`/api${path}`, { method, headers, body: payload });
  let data = null;
  try { data = await res.json(); } catch { /* empty body */ }
  if (!res.ok) {
    const e = new Error((data && data.error) || `Request failed (${res.status})`);
    e.status = res.status; e.data = data; throw e;
  }
  return data;
}

let toastTimer;
export function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTimer); toastTimer = setTimeout(() => t.classList.remove("show"), 3200);
}

export function modal(title, content, actions = []) {
  const d = $("#modal");
  d.replaceChildren(
    h("h2", null, title), content,
    h("div", { class: "row", style: "margin-top:1rem;justify-content:flex-end" },
      h("button", { class: "ghost", onclick: () => d.close() }, "Close"), ...actions));
  d.showModal();
  return d;
}
export const closeModal = () => $("#modal").close();

export const LABEL = { verified: "Verified", discrepancy: "Discrepancy", missing_information: "Missing information", actively_bad: "Actively bad",
  check_failed: "Check failed", unchecked: "Not checked yet", pending: "Pending", applied: "Applied", rejected: "Rejected", open: "Open", accepted: "Accepted",
  declined: "Declined", closed: "Closed", approved: "Approved", completed: "Completed", failed: "Failed", running: "Running" };
export const badge = (k) => h("span", { class: `badge b-${k}` }, LABEL[k] || k);
export const fmtDate = (s) => (s ? new Date(s).toLocaleString(undefined, { dateStyle: "medium", timeStyle: s.length > 10 ? "short" : undefined }) : "—");
export const fmtDay = (s) => (s ? new Date(s).toLocaleDateString(undefined, { dateStyle: "medium" }) : "—");

export function empty(msg, action) { return h("div", { class: "empty" }, h("p", null, msg), action || null); }
export function errBox(e) {
  const d = e.data || {};
  return h("div", { class: "err", role: "alert" }, e.message, d.details ? h("ul", null, d.details.map((x) => h("li", null, x))) : null);
}
export function table(headers, rows, emptyMsg = "Nothing here yet.") {
  if (!rows.length) return h("div", { class: "tablewrap" }, empty(emptyMsg));
  return h("div", { class: "tablewrap" }, h("table", null,
    h("thead", null, h("tr", null, headers.map((x) => h("th", null, x)))),
    h("tbody", null, rows.map((r) => h("tr", null, r.map((c) => h("td", null, c)))))));
}
export function tabs(items, current) {
  return h("nav", { class: "tabs", "aria-label": "Sections" }, items.map(([href, label]) => h("a", { href, "aria-current": href === current ? "page" : null }, label)));
}

// Human-readable summary of an HSDS value for diffs (arrays of phones, schedules, areas...)
export function summarize(v) {
  if (v == null || v === "") return "(empty)";
  if (Array.isArray(v)) {
    if (!v.length) return "(none)";
    return v.map((x) => (typeof x === "object" ? summarize(x) : String(x))).join("; ");
  }
  if (typeof v === "object") {
    if (v.number) return `${v.number}${v.description ? ` (${v.description})` : ""}`;
    if (v.byday) return `${v.byday} ${String(v.opens_at || "").slice(0, 5)}–${String(v.closes_at || "").slice(0, 5)}`;
    if (v.document) return v.document;
    if (v.name) return v.name;
    if (v.notes || v.description) return v.notes || v.description;
    return JSON.stringify(v);
  }
  return String(v);
}
export function diff(field, cur, prop) {
  return h("div", null, h("strong", null, field.replaceAll("_", " ")),
    h("div", { class: "diff" },
      h("div", { class: "was" }, h("small", null, "Now"), summarize(cur)),
      h("div", { class: "now" }, h("small", null, "Proposed"), summarize(prop))));
}
export function pager(total, page, per, go) {
  const pages = Math.max(1, Math.ceil(total / per));
  return h("div", { class: "row spread", style: "margin-top:.75rem" },
    h("span", { class: "muted small" }, `${total} total · page ${page} of ${pages}`),
    h("div", { class: "row" },
      h("button", { class: "ghost sm", disabled: page <= 1, onclick: () => go(page - 1) }, "Previous"),
      h("button", { class: "ghost sm", disabled: page >= pages, onclick: () => go(page + 1) }, "Next")));
}
export function params() {
  const q = location.hash.split("?")[1] || "";
  return Object.fromEntries(new URLSearchParams(q));
}
export function qs(o) {
  const p = new URLSearchParams(); Object.entries(o).forEach(([k, v]) => v !== "" && v != null && p.set(k, v));
  const s = p.toString(); return s ? `?${s}` : "";
}
