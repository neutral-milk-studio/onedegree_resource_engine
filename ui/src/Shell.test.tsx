import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Shell } from "./Shell";

describe("Shell", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("marks the current page in the navigation", () => {
    render(
      <Shell page="conflicts" userName="Sam" isDemo={false} onSignOut={() => {}}>
        <p>content</p>
      </Shell>,
    );
    const nav = screen.getByRole("navigation", { name: "Main" });
    expect(nav.querySelector('[aria-current="page"]')).toHaveTextContent("Conflicts");
    expect(screen.getByRole("link", { name: "Skip to content" })).toHaveAttribute("href", "#main");
  });

  it("switches to dark mode and remembers the choice", async () => {
    const user = userEvent.setup();
    render(
      <Shell page="overview" userName="Sam" isDemo onSignOut={() => {}}>
        <p>content</p>
      </Shell>,
    );
    const [darkButton] = screen.getAllByRole("button", { name: "Dark" });
    await user.click(darkButton);
    expect(document.documentElement).toHaveAttribute("data-theme", "dark");
    expect(localStorage.getItem("re-theme")).toBe("dark");
    expect(darkButton).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("status")).toHaveTextContent("Demo data");
  });
});
