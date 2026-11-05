// GET /coverage: which common needs have no visible, active service in each city.
import { ApiError, type Session } from "./client";

export interface CityCoverage {
  city: string;
  service_count: number;
  covered: string[];
  missing: string[];
}

export interface CoverageResponse {
  needs: string[];
  cities: CityCoverage[];
}

export interface CoverageApi {
  readonly isDemo: boolean;
  getCoverage(): Promise<CoverageResponse>;
}

const API_BASE: string = import.meta.env.VITE_API_BASE ?? "";

export function createLiveCoverageApi(token: string): CoverageApi {
  return {
    isDemo: false,
    async getCoverage() {
      let response: Response;
      try {
        response = await fetch(`${API_BASE}/coverage`, { headers: { Authorization: `Bearer ${token}` } });
      } catch {
        throw new ApiError(0, "Can't reach the Resource Engine API. Is it running?");
      }
      if (!response.ok) {
        const body: { detail?: unknown } = await response.json().catch(() => ({}));
        const message = typeof body.detail === "string" ? body.detail : `Request failed (${response.status}).`;
        throw new ApiError(response.status, message);
      }
      return (await response.json()) as CoverageResponse;
    },
  };
}

const NEEDS = ["Rent and housing costs", "Job loss and income", "Food", "Shelter", "Bills and utilities", "Legal help", "Health", "Older adults"];

/** Demo mode: a couple of simulated cities, one with a visible gap. */
export function createMockCoverageApi(): CoverageApi {
  return {
    isDemo: true,
    async getCoverage() {
      return {
        needs: NEEDS,
        cities: [
          { city: "Oakland", service_count: 12, covered: NEEDS.filter((n) => n !== "Legal help"), missing: ["Legal help"] },
          { city: "San Leandro", service_count: 3, covered: ["Food", "Shelter"], missing: NEEDS.filter((n) => n !== "Food" && n !== "Shelter") },
        ],
      };
    },
  };
}

export function createCoverageApi(session: Session): CoverageApi {
  return import.meta.env.VITE_API_MODE === "live" ? createLiveCoverageApi(session.token) : createMockCoverageApi();
}
