import { describe, expect, it } from "vitest";
import { badge, h, qs, safeHref, summarize, table } from "./ui.js";

describe("h", () => {
  it("writes data as text, never as markup", () => {
    const el = h("div", null, "<img src=x onerror=alert(1)>");
    expect(el.querySelector("img")).toBeNull();
    expect(el.textContent).toContain("<img");
  });

  it("drops empty children and attributes", () => {
    const el = h("p", { title: null, hidden: false }, null, false, "kept");
    expect(el.attributes.length).toBe(0);
    expect(el.textContent).toBe("kept");
  });
});

describe("safeHref", () => {
  it.each(["javascript:alert(1)", " JAVASCRIPT:alert(1)", "data:text/html,x", "vbscript:x"])("blocks %s", (url) => {
    expect(safeHref(url)).toBe("#");
    expect(h("a", { href: url }).getAttribute("href")).toBe("#");
  });

  it.each(["https://example.org/a", "http://example.org", "#/admin", "/styles.css"])("allows %s", (url) => {
    expect(safeHref(url)).toBe(url);
  });
});

describe("summarize", () => {
  it("reads HSDS lists the way a reviewer would", () => {
    expect(summarize([{ number: "415-555-0101" }, { name: "Spanish" }])).toBe("415-555-0101; Spanish");
    expect(summarize({ byday: "MO", opens_at: "09:00", closes_at: "17:00" })).toBe("MO 09:00–17:00");
    expect(summarize(null)).toBe("(empty)");
    expect(summarize([])).toBe("(none)");
  });
});

describe("table", () => {
  it("shows the empty message when there are no rows", () => {
    expect(table(["A"], [], "Nothing yet").textContent).toContain("Nothing yet");
  });
});

it("qs leaves out empty values", () => {
  expect(qs({ a: 1, b: "", c: null })).toBe("?a=1");
  expect(qs({})).toBe("");
});

it("badge labels known states", () => {
  expect(badge("actively_bad").textContent).toBe("Actively bad");
});
