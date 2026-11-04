import { beforeEach, describe, expect, it, vi } from "vitest";

const respond = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

async function start(routes, hash) {
  vi.resetModules();
  document.body.innerHTML = `<header id="top"></header><main id="main" tabindex="-1"><div id="app"></div></main>
    <div id="toast"></div><dialog id="modal"></dialog>`;
  localStorage.clear();
  location.hash = hash;
  globalThis.fetch = vi.fn(async (url) => routes[url] ?? respond({ error: "not found" }, 404));
  await import("./app.js");
  await vi.waitFor(() => expect(document.querySelector("#app").textContent).not.toContain("Loading"));
}

describe("login page", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows demo accounts in a local run", async () => {
    await start({ "/api/health": respond({ demo: true }) }, "#/login");
    await vi.waitFor(() => expect(document.body.textContent).toContain("Demo accounts"));
  });

  it("hides demo accounts when deployed", async () => {
    await start({ "/api/health": respond({ demo: false }) }, "#/login");
    await vi.waitFor(() => expect(document.body.textContent).toContain("Log in"));
    expect(document.body.textContent).not.toContain("demo-admin-1");
  });

  it("hides demo accounts when the health check fails", async () => {
    await start({}, "#/login");
    await vi.waitFor(() => expect(document.body.textContent).toContain("Log in"));
    expect(document.body.textContent).not.toContain("demo-admin-1");
  });
});
