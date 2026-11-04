import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { createMockConflictsApi, type ConflictsApi } from "../api/conflicts";
import { Conflicts } from "./Conflicts";

function renderWith(api: ConflictsApi) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Conflicts api={api} />
    </QueryClientProvider>,
  );
}

describe("Conflicts", () => {
  it("shows both sources side by side with the services each value affects", async () => {
    renderWith(createMockConflictsApi("Sam"));
    const card = await screen.findByRole("article");
    expect(within(card).getByRole("heading")).toHaveTextContent("Demo Community Center: email");
    expect(card).toHaveTextContent("211 (NDP) says");
    expect(card).toHaveTextContent("admin@demo.example");
    expect(card).toHaveTextContent("One Degree (ODRS) says");
    expect(card).toHaveTextContent("Used by 2 services: Food Pantry, Rent Help");
    expect(within(card).getByRole("button", { name: "Use this value" })).toBeDisabled();
  });

  it("applies the chosen value and moves the conflict to Resolved", async () => {
    const user = userEvent.setup();
    const api = createMockConflictsApi("Sam");
    const resolve = vi.spyOn(api, "resolve");
    renderWith(api);
    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("radio", { name: /211 \(NDP\) says/ }));
    await user.click(within(card).getByRole("button", { name: "Use this value" }));
    expect(resolve).toHaveBeenCalledWith("demo-c1", "a", undefined);
    expect(await screen.findByRole("status")).toHaveTextContent("updated 1 record to use admin@demo.example");
    expect(await screen.findByText("No open conflicts. The sources agree.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Resolved" }));
    expect(await screen.findByText("Sam chose admin@demo.example.")).toBeInTheDocument();
  });

  it("asks for a value when neither source is right", async () => {
    const user = userEvent.setup();
    renderWith(createMockConflictsApi("Sam"));
    const card = await screen.findByRole("article");
    await user.click(within(card).getByRole("radio", { name: /Neither/ }));
    await user.click(within(card).getByRole("button", { name: "Use this value" }));
    expect(await within(card).findByRole("alert")).toHaveTextContent("Enter the value to use.");
  });
});
