import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { createMockPartnersApi, plainReason, type PartnersApi } from "../api/partners";
import { AddPartner } from "./AddPartner";

function renderWith(api: PartnersApi) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AddPartner api={api} />
    </QueryClientProvider>,
  );
}

async function toMapping(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: "Use the sample county food pantry file" }));
  await user.click(await screen.findByRole("button", { name: "Suggest a mapping" }));
  return screen.findByRole("region", { name: "Where each column goes" });
}

describe("AddPartner", () => {
  it("suggests a mapping with confidence and flags low-confidence columns", async () => {
    const user = userEvent.setup();
    renderWith(createMockPartnersApi("Sam"));
    const section = await toMapping(user);
    const rows = within(section).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByRole("combobox")).toHaveValue("service_name");
    expect(rows[0]).toHaveTextContent("High · 90%");
    expect(rows[2]).toHaveTextContent("Low: please check · 30%");
    expect(screen.getByText(/Elapsed:/)).toBeInTheDocument();
  });

  it("sends the edited mapping and shows counts and plain reasons", async () => {
    const user = userEvent.setup();
    const api = createMockPartnersApi("Sam");
    const confirm = vi.spyOn(api, "confirm");
    renderWith(api);
    const section = await toMapping(user);
    await user.selectOptions(within(section).getByRole("combobox", { name: "Field for column Notes" }), "description");
    expect(section).toHaveTextContent("You changed 1.");
    await user.click(within(section).getByRole("button", { name: /Confirm and import 7 rows/ }));

    expect(confirm).toHaveBeenCalledWith(
      "demo-upload",
      "Solano County Food Pantries",
      { "Pantry Name": "service_name", "Phone #": "phone", Notes: "description" },
      "CA",
    );
    const result = await screen.findByRole("status");
    expect(result).toHaveTextContent("Solano County Food Pantries added in");
    expect(result).toHaveTextContent("Rows received7");
    expect(result).toHaveTextContent("7 = 7 + 0");
    expect(result).toHaveTextContent("The street address is missing.");
    expect(result).toHaveTextContent("“call the office” isn't a phone number.");
  });

  it("explains a rejected file", async () => {
    // The input's accept=".csv" already filters in a browser; bypass it to test the server's message.
    const user = userEvent.setup({ applyAccept: false });
    renderWith(createMockPartnersApi("Sam"));
    await user.type(screen.getByLabelText("Partner name"), "Acme");
    await user.upload(screen.getByLabelText("Spreadsheet (CSV)"), new File(["x"], "list.xlsx"));
    await user.click(screen.getByRole("button", { name: "Suggest a mapping" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Upload a CSV file.");
  });
});

describe("plainReason", () => {
  it("translates validator messages", () => {
    expect(plainReason("address", "(root): 'postal_code' is a required property")).toBe("The ZIP code is missing.");
    expect(plainReason("record", "something odd")).toBe("record: something odd");
  });
});
