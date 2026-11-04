import { state, h, $, api, toast, modal, closeModal, badge, errBox, fmtDate, fmtDay, empty, table, tabs, params, qs, LABEL } from "./ui.js";
import { recordEditor } from "./records.js";
import { admin } from "./admin.js";

const app = $("#app");
const ROLE_HOME = { seeker: "#/", provider: "#/provider", admin: "#/admin" };
let chatSession = sessionStorage.getItem("re_chat") || null;
let chatLog = [];

// ---------------------------------------------------------------------------------------- shell
function renderTop() {
  const u = state.user, r = location.hash.split("?")[0] || "#/";
  const link = (href, label) => h("a", { href, "aria-current": r === href ? "page" : null }, label);
  const nav = [link("#/", "Ask for help")];
  if (u?.role === "seeker") nav.push(link("#/requests", "My requests"), link("#/profile", "My profile"));
  if (u?.role === "provider") nav.push(link("#/provider", "My organization"), link("#/provider/needs", "Needs & people served"), link("#/provider/documents", "Documents"));
  if (u?.role === "admin") nav.push(link("#/admin", "Overview"), link("#/admin/review", "Review"), link("#/admin/records", "Records"), link("#/admin/providers", "Providers"),
    link("#/admin/seekers", "Help seekers"), link("#/admin/imports", "Imports"), link("#/admin/documents", "Documents"), link("#/admin/keys", "API keys"));
  $("#top").replaceChildren(h("div", { class: "bar" },
    h("a", { class: "brand", href: "#/" }, h("i"), "One Degree"), h("nav", { "aria-label": "Main" }, nav),
    h("div", { class: "who" }, u ? [h("span", null, `${u.name} · ${u.role}`), h("a", { href: "#/", onclick: logout }, "Log out")]
      : [h("a", { href: "#/login" }, "Log in"), h("a", { href: "#/signup" }, "Create a profile")])));
}
function logout(e) {
  e?.preventDefault(); state.token = null; state.user = null; localStorage.removeItem("re_token");
  chatSession = null; chatLog = []; sessionStorage.removeItem("re_chat"); location.hash = "#/"; boot();
}
async function loadUser() {
  if (!state.token) return;
  try { state.user = await api("/auth/me"); } catch { state.token = null; localStorage.removeItem("re_token"); }
}
async function loadHealth() {
  try { state.demo = (await api("/health")).demo === true; } catch { state.demo = false; }
}
function setSession(res) {
  state.token = res.token; state.user = res.user; localStorage.setItem("re_token", res.token);
}
const page = (...kids) => h("div", { class: "wrap" }, ...kids);
const need = (...roles) => { if (!state.user || !roles.includes(state.user.role)) { location.hash = "#/login"; return false; } return true; };

// ---------------------------------------------------------------------------------------- home + chat
function bubble(m) {
  const box = h("div", { class: `msg ${m.role === "user" ? "user" : "bot"}` }, m.content);
  const wrap = h("div", { style: "display:flex;flex-direction:column" }, box);
  (m.cards || []).forEach((c) => wrap.append(h("div", { class: "svc" },
    h("h4", null, c.name), h("div", { class: "muted small" }, c.organization),
    h("p", { class: "small" }, c.description),
    h("div", { class: "small" }, [c.phone && `Call ${c.phone}`, c.address, c.eligibility && `Who can use it: ${c.eligibility}`,
      c.last_verified && `Last verified ${fmtDay(c.last_verified)}`].filter(Boolean).map((x) => h("div", null, x))),
    h("div", { class: "row", style: "margin-top:.4rem" },
      (m.actions || []).filter((a) => a.service_id === c.id).map((a) => h("button", { class: "sm", onclick: () => sendRequest(a.service_id, c.organization) }, a.label)),
      c.apply_url && h("a", { class: "btn ghost sm", href: c.apply_url, target: "_blank", rel: "noopener" }, "Apply online")))));
  (m.citations || []).forEach((c) => wrap.append(h("div", { class: "small muted", style: "margin:.3rem .3rem 0" }, `Source: ${c.title}${c.effective_date ? `, effective ${fmtDay(c.effective_date)}` : ""}`)));
  (m.actions || []).filter((a) => a.type === "signup" || a.type === "login").forEach((a) => {
    if (!wrap.querySelector(".row.acts")) wrap.append(h("div", { class: "row acts", style: "margin-top:.4rem" }));
    wrap.querySelector(".row.acts").append(h("a", { class: a.type === "signup" ? "btn" : "btn ghost", href: `#/${a.type === "signup" ? "signup" : "login"}?from=chat` }, a.label));
  });
  return wrap;
}
async function sendRequest(serviceId, orgName) {
  try {
    const r = await api("/chat/confirm", { method: "POST", body: { session_id: chatSession, service_id: serviceId } });
    chatLog.push({ role: "assistant", content: r.reply }); toast("Request sent."); drawLog();
  } catch (e) {
    if (e.data?.code === "consent_required") {
      modal("Share your details with this provider?",
        h("p", null, `${orgName} will see your first name, your ZIP code and what you need. After they accept, they can see how to reach you. You can change this later in your profile.`),
        [h("button", { onclick: async () => { await api("/seeker/profile", { method: "PUT", body: { share_with_providers: true } }); closeModal(); sendRequest(serviceId, orgName); } }, "Share and send request")]);
    } else toast(e.message);
  }
}
let logEl;
function drawLog() { if (!logEl) return; logEl.replaceChildren(...chatLog.map(bubble)); logEl.scrollTop = logEl.scrollHeight; }
async function say(text, input) {
  if (!text.trim()) return;
  chatLog.push({ role: "user", content: text }); drawLog(); if (input) input.value = "";
  try {
    const r = await api("/chat", { method: "POST", body: { session_id: chatSession, message: text } });
    chatSession = r.session_id; sessionStorage.setItem("re_chat", chatSession);
    chatLog.push({ role: "assistant", content: r.reply, cards: r.cards, citations: r.citations, actions: r.actions });
  } catch (e) { chatLog.push({ role: "assistant", content: e.message }); }
  drawLog();
}
async function home() {
  const u = state.user;
  if (u && u.role !== "seeker") { location.hash = ROLE_HOME[u.role]; return; }
  if (!chatLog.length) chatLog.push({ role: "assistant", content: u ? `Hi ${u.name.split(" ")[0]}. What do you need help with today?` : "Tell me what you need in your own words. I can also answer questions about how One Degree works." });
  const input = h("input", { placeholder: "For example: I don't have money for diapers", "aria-label": "Message", autocomplete: "off" });
  logEl = h("div", { class: "log", "aria-live": "polite" });
  const send = () => say(input.value, input);
  input.addEventListener("keydown", (e) => e.key === "Enter" && send());
  const chips = ["I don't have money for diapers", "I'm behind on rent", "Where can I get free groceries?", "What do I need to bring for the diaper bank?", "How does One Degree work?"];
  app.replaceChildren(page(h("div", { class: "hero" },
    h("section", null, h("h1", null, "Find free and low‑cost help near you."),
      h("p", { class: "muted", style: "max-width:46ch;font-size:1.05rem" }, "One Degree connects people to food, housing, health care, legal help and more. Say what you need and we will find providers who can help."),
      h("div", { class: "chat" }, logEl,
        h("div", { class: "chips" }, chips.map((c) => h("button", { class: "chip", onclick: () => say(c) }, c))),
        h("div", { class: "compose" }, input, h("button", { onclick: send }, "Send"))),
      h("p", { class: "hint" }, "In an emergency call 911. For crisis support call or text 988. Dial 211 to talk to a person.")),
    h("aside", null, h("h2", null, "How it works"),
      h("ol", { class: "steps" }, h("li", null, h("div", null, h("strong", null, "Tell us what you need."), h("div", { class: "muted small" }, "No sign-up needed to ask questions about One Degree or its policies."))),
        h("li", null, h("div", null, h("strong", null, "Create a free profile."), h("div", { class: "muted small" }, "We need it to match you with providers and send a request for you. You choose what to share."))),
        h("li", null, h("div", null, h("strong", null, "Providers respond."), h("div", { class: "muted small" }, "Each listing shows when it was last verified, so you know how fresh it is.")))),
      h("div", { class: "card flat" }, h("strong", null, "Are you a service provider?"), h("p", { class: "muted small" }, "Log in to keep your listing current, see people who need what you offer, and share your policies."), h("a", { class: "btn ghost sm", href: "#/login" }, "Provider log in"))))));
  drawLog();
  if (u && chatSession && params().resume) { const r = await api("/chat/resume", { method: "POST", body: { session_id: chatSession } }); chatLog.push({ role: "assistant", content: r.reply, cards: r.cards, actions: r.actions }); drawLog(); }
}

// ---------------------------------------------------------------------------------------- auth
function authForm(kind) {
  const signup = kind === "signup", fromChat = params().from === "chat";
  const err = h("div"), name = h("input", { autocomplete: "name" }), email = h("input", { type: "email", autocomplete: "email" }),
    pw = h("input", { type: "password", autocomplete: signup ? "new-password" : "current-password" });
  const go = async () => {
    err.replaceChildren();
    try {
      const res = await api(signup ? "/auth/register" : "/auth/login", { method: "POST", body: { name: name.value, email: email.value, password: pw.value } });
      setSession(res); await boot(true);
      location.hash = fromChat && res.user.role === "seeker" ? "#/?resume=1" : ROLE_HOME[res.user.role];
    } catch (e) { err.replaceChildren(errBox(e)); }
  };
  app.replaceChildren(page(h("div", { class: "narrow" }, h("h1", null, signup ? "Create your profile" : "Log in"),
    h("p", { class: "muted" }, signup ? "It is free. We use it to match you with providers and send requests for you." : "Seekers, providers and One Degree staff use the same form."),
    err, signup && [h("label", null, "Your name"), name], h("label", null, "Email"), email, h("label", null, "Password"), pw,
    signup && h("p", { class: "hint" }, "Use at least 8 characters."),
    h("div", { class: "row", style: "margin-top:1rem" }, h("button", { onclick: go }, signup ? "Create profile" : "Log in"),
      h("a", { href: signup ? "#/login" : "#/signup" + (fromChat ? "?from=chat" : "") }, signup ? "I already have a profile" : "Create a profile")),
    !signup && state.demo && h("div", { class: "card flat small", style: "margin-top:1.5rem" }, h("strong", null, "Demo accounts"),
      h("div", null, "admin@demo.org / demo-admin-1"), h("div", null, "provider1@demo.org / demo-provider-1"), h("div", null, "ana@demo.org / demo-seeker-1")))));
}

// ---------------------------------------------------------------------------------------- seeker
async function profile() {
  if (!need("seeker")) return;
  const p = await api("/seeker/profile");
  const f = (k, label, type = "text") => [h("label", null, label), h("input", { id: `p_${k}`, type, value: p[k] ?? "" })];
  const share = h("input", { type: "checkbox", id: "p_share", checked: p.share_with_providers ? true : null });
  const out = h("div");
  app.replaceChildren(page(h("div", { class: "narrow" }, h("h1", null, "My profile"),
    h("p", { class: "muted" }, "Providers only see your first name, ZIP code and need, and only if you agree below."),
    out, ...f("zip", "ZIP code"), ...f("phone", "Phone (optional)", "tel"), ...f("language", "Preferred language"), ...f("household_size", "People in your household", "number"),
    h("label", null, "Preferred contact method"), h("select", { id: "p_contact_preference" }, ["email", "phone", "text message"].map((o) => h("option", { value: o, selected: p.contact_preference === o ? true : null }, o))),
    h("div", { class: "row", style: "margin-top:1rem" }, share, h("label", { for: "p_share", style: "margin:0;font-weight:500" }, "Let providers I contact see my first name, ZIP code and need")),
    h("div", { class: "row", style: "margin-top:1rem" }, h("button", { onclick: async () => {
      const body = { share_with_providers: share.checked, contact_preference: $("#p_contact_preference").value };
      ["zip", "phone", "language"].forEach((k) => (body[k] = $(`#p_${k}`).value));
      body.household_size = Number($("#p_household_size").value) || null;
      await api("/seeker/profile", { method: "PUT", body }); toast("Profile saved.");
    } }, "Save profile")))));
}
async function requests() {
  if (!need("seeker")) return;
  const { requests } = await api("/seeker/requests");
  app.replaceChildren(page(h("h1", null, "My requests"), table(["Sent", "What you asked for", "Provider", "Status"],
    requests.map((r) => [fmtDate(r.created_at), r.need_text, h("span", null, r.org_name || "Waiting for a match", r.service_name && h("div", { class: "muted small" }, r.service_name)), badge(r.status)]),
    "You have not sent a request yet. Ask for help on the home page and choose a provider.")));
}

// ---------------------------------------------------------------------------------------- provider
async function providerHome() {
  if (!need("provider")) return;
  let d; try { d = await api("/provider/organization"); } catch (e) { app.replaceChildren(page(errBox(e))); return; }
  const open = (rec, label) => async () => {
    const body = await recordEditor(rec.id, { mode: "provider", onSaved: () => { closeModal(); providerHome(); } });
    modal(`Edit ${label}`, body);
  };
  const org = d.organization;
  app.replaceChildren(page(h("div", { class: "row spread" }, h("div", null, h("h1", null, org.data.name), h("p", { class: "muted" }, "Keep your listing accurate. Changes are reviewed by One Degree before they go live.")),
    h("button", { onclick: open(org, "organization") }, "Edit organization")),
    h("h2", { style: "margin-top:1.5rem" }, "Your services"),
    table(["Service", "Status", "Last check", "Waiting for review", ""], d.services.map((s) => [s.data.name, s.data.status, s.check ? badge(s.check.category) : badge("unchecked"),
      s.pending ? badge("pending") : "—", h("button", { class: "sm ghost", onclick: open(s, s.data.name) }, "Edit")])),
    h("h2", { style: "margin-top:1.5rem" }, "Your locations"),
    table(["Location", "Address", "Waiting for review", ""], d.locations.map((l) => [l.data.name || "Location", (l.data.addresses || []).map((a) => `${a.address_1}, ${a.city}`).join("; ") || "—",
      l.pending ? badge("pending") : "—", h("button", { class: "sm ghost", onclick: open(l, l.data.name || "location") }, "Edit")]))));
}
async function providerNeeds() {
  if (!need("provider")) return;
  const [n, s] = await Promise.all([api("/provider/needs"), api("/provider/served")]);
  const act = (id, action, svc) => async () => { await api(`/provider/requests/${id}/respond`, { method: "POST", body: { action, service_id: svc } }); toast("Updated."); providerNeeds(); };
  const card = (c, sug) => [h("strong", null, c.first_name), h("div", { class: "muted small" }, [c.zip, c.language].filter(Boolean).join(" · ")), c.need,
    sug ? h("span", { class: "muted small" }, `Matches ${c.suggested_service}`) : "", fmtDate(c.created_at),
    h("div", { class: "row" }, h("button", { class: "sm", onclick: act(c.request_id, "accept", c.suggested_service_id) }, "Offer support"), h("button", { class: "sm danger", onclick: act(c.request_id, "decline") }, "Decline"))];
  const rows = (list, sug) => list.map((c) => { const [a, b, need, extra, d, btns] = card(c, sug); return [h("div", null, a, b), need, extra, d, btns]; });
  app.replaceChildren(page(h("h1", null, "People who need help"),
    h("p", { class: "muted" }, "You see a first name, ZIP code and need only for people who agreed to share. Contact details appear after you offer support."),
    h("h2", null, "Asked for your organization"), table(["Person", "Need", "", "Asked", ""], rows(n.direct, false), "No open requests right now."),
    h("h2", { style: "margin-top:1.5rem" }, "Might be a match for what you offer"), table(["Person", "Need", "Why", "Asked", ""], rows(n.suggested, true), "No unassigned needs match your services."),
    h("h2", { style: "margin-top:1.5rem" }, "People you have supported"),
    table(["Person", "How to reach", "Need", "Service", "Status", ""], s.served.map((c) => [c.name || c.first_name, `${c.email || ""}${c.phone ? " · " + c.phone : ""}${c.contact_preference ? ` (prefers ${c.contact_preference})` : ""}`, c.need, c.service || "—", badge(c.status),
      c.status === "accepted" ? h("button", { class: "sm ghost", onclick: act(c.request_id, "close") }, "Mark done") : ""]), "Nobody yet. Offer support above to see people here.")));
}
async function providerDocs() {
  if (!need("provider")) return;
  const { documents } = await api("/provider/documents");
  const file = h("input", { type: "file", accept: ".pdf,.docx,.txt,.md,.html" }), title = h("input", { placeholder: "e.g. Rental assistance eligibility policy" }), eff = h("input", { type: "date" });
  const out = h("div");
  app.replaceChildren(page(h("h1", null, "Policy documents"), h("p", { class: "muted" }, "Upload eligibility rules, intake policies or forms. After One Degree approves them, the chat assistant uses them to answer questions and cites your document."),
    h("div", { class: "card", style: "max-width:560px" }, out, h("label", null, "Document"), file, h("label", null, "Title"), title, h("label", null, "Effective date"), eff,
      h("div", { style: "margin-top:1rem" }, h("button", { onclick: async () => {
        out.replaceChildren(); const fd = new FormData(); if (!file.files[0]) return toast("Choose a file first.");
        fd.append("file", file.files[0]); fd.append("title", title.value || file.files[0].name); fd.append("effective_date", eff.value);
        try { const d = await api("/provider/documents", { method: "POST", form: fd }); toast(d.chunk_count ? "Uploaded. Waiting for approval." : "Uploaded, but no readable text was found."); providerDocs(); }
        catch (e) { out.replaceChildren(errBox(e)); }
      } }, "Upload"))),
    h("h2", { style: "margin-top:1.5rem" }, "Your documents"),
    table(["Title", "File", "Effective", "Status", "Notes"], documents.map((d) => [d.title, d.filename, fmtDay(d.effective_date), badge(d.status), d.extraction === "ok" ? "" : d.extraction]), "No documents yet.")));
}

// ---------------------------------------------------------------------------------------- router
const routes = {
  "": home, "/": home, "/login": () => authForm("login"), "/signup": () => authForm("signup"), "/profile": profile, "/requests": requests,
  "/provider": providerHome, "/provider/needs": providerNeeds, "/provider/documents": providerDocs,
  ...admin(page, need),
};
async function route() {
  renderTop();
  const [path] = (location.hash.slice(1) || "/").split("?");
  const dyn = [["/admin/record/", "/admin/record/:id"], ["/admin/provider/", "/admin/provider/:id"], ["/admin/seeker/", "/admin/seeker/:id"]].find(([p]) => path.startsWith(p));
  const handler = dyn ? routes[dyn[1]] : routes[path];
  app.replaceChildren(h("div", { class: "wrap muted" }, "Loading…"));
  try { await (handler ? handler(dyn ? path.slice(dyn[0].length) : undefined) : home()); }
  catch (e) { if (e.status === 401) return logout(); app.replaceChildren(page(errBox(e))); }
  $("#main").focus({ preventScroll: true });
}
async function boot(skipRoute) { await loadHealth(); await loadUser(); if (!skipRoute) route(); else renderTop(); }
window.addEventListener("hashchange", route);
boot();
