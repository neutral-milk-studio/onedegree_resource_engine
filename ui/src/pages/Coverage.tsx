// Coverage: which common needs have no visible, active service in each city. A read of
// data already ingested -- no new taxonomy, just a gap report for staff.
import { useQuery } from "@tanstack/react-query";

import { ApiError } from "../api/client";
import type { CoverageApi } from "../api/coverage";

export function Coverage({ api }: { api: CoverageApi }) {
  const coverage = useQuery({ queryKey: ["coverage"], queryFn: () => api.getCoverage() });

  if (coverage.isError) {
    const error = coverage.error;
    return (
      <div className="p-4 lg:p-6">
        <p className="alert" role="alert">
          Couldn't load coverage. {error instanceof ApiError ? error.message : "Try again."}
        </p>
      </div>
    );
  }

  const data = coverage.data;
  const citiesWithGaps = data?.cities.filter((city) => city.missing.length > 0) ?? [];

  return (
    <div className="mx-auto flex max-w-5xl flex-col gap-4 p-4 lg:p-6">
      <header>
        <h1 className="text-2xl font-semibold">Coverage</h1>
        <p className="text-sm text-muted">
          Which common needs have no visible, active listing in each city — a gap report built from the data
          already ingested, not a new survey.
        </p>
      </header>

      {coverage.isLoading && <p className="text-sm text-muted">Loading…</p>}

      {data && data.cities.length === 0 && <p className="text-sm text-muted">No cities with listings yet.</p>}

      {data && data.cities.length > 0 && (
        <>
          <p className="text-sm" role="status">
            {citiesWithGaps.length === 0
              ? "Every city has at least one listing for every need we track."
              : `${citiesWithGaps.length} of ${data.cities.length} ${data.cities.length === 1 ? "city has" : "cities have"} at least one gap.`}
          </p>

          <div className="overflow-x-auto rounded-xl border border-line">
            <table className="w-full min-w-[640px] border-collapse text-sm">
              <thead>
                <tr className="border-b border-line bg-surface-2 text-left">
                  <th scope="col" className="p-2.5 font-medium">
                    City
                  </th>
                  <th scope="col" className="p-2.5 font-medium">
                    Listings
                  </th>
                  {data.needs.map((need) => (
                    <th key={need} scope="col" className="p-2.5 text-center font-medium" title={need}>
                      <span className="visually-hidden">{need}</span>
                      <span aria-hidden="true">{need.split(" ")[0]}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.cities.map((city) => (
                  <tr key={city.city} className="border-b border-line last:border-0">
                    <th scope="row" className="p-2.5 text-left font-medium">
                      {city.city}
                    </th>
                    <td className="p-2.5">{city.service_count}</td>
                    {data.needs.map((need) => {
                      const covered = city.covered.includes(need);
                      return (
                        <td key={need} className="p-2.5 text-center">
                          <span
                            className={covered ? "text-good" : "text-urgent"}
                            role="img"
                            aria-label={`${need}: ${covered ? "covered" : "no listing"} in ${city.city}`}
                          >
                            {covered ? "✓" : "—"}
                          </span>
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
