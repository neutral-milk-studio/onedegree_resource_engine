import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";

import type { CoverageApi, CoverageResponse } from "../api/coverage";
import { Coverage } from "./Coverage";

function renderWith(api: CoverageApi) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <Coverage api={api} />
    </QueryClientProvider>,
  );
}

const DATA: CoverageResponse = {
  needs: ["Food", "Legal help"],
  cities: [
    { city: "Oakland", service_count: 12, covered: ["Food", "Legal help"], missing: [] },
    { city: "San Leandro", service_count: 3, covered: ["Food"], missing: ["Legal help"] },
  ],
};

const staticApi = (data: CoverageResponse): CoverageApi => ({
  isDemo: false,
  getCoverage: async () => data,
});

describe("Coverage", () => {
  it("shows a row per city and a column per need", async () => {
    renderWith(staticApi(DATA));
    expect(await screen.findByText("Oakland")).toBeInTheDocument();
    expect(screen.getByText("San Leandro")).toBeInTheDocument();
    expect(screen.getByText(/1 of 2 cities have at least one gap/)).toBeInTheDocument();
  });

  it("marks a missing need for the right city, not every city", async () => {
    renderWith(staticApi(DATA));
    await screen.findByText("Oakland");
    expect(screen.getByLabelText("Legal help: no listing in San Leandro")).toBeInTheDocument();
    expect(screen.getByLabelText("Legal help: covered in Oakland")).toBeInTheDocument();
  });

  it("says when nothing is missing anywhere", async () => {
    renderWith(
      staticApi({
        needs: ["Food"],
        cities: [{ city: "Oakland", service_count: 5, covered: ["Food"], missing: [] }],
      }),
    );
    expect(await screen.findByText(/Every city has at least one listing/)).toBeInTheDocument();
  });

  it("explains a failed load", async () => {
    const api: CoverageApi = { isDemo: false, getCoverage: async () => Promise.reject(new Error("boom")) };
    renderWith(api);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load coverage");
  });
});
