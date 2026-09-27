export type HealthState = "healthy" | "error";

export interface HealthStatus {
  state: HealthState;
  detail: string;
}

export interface PlatformHealth {
  backend: HealthStatus;
  database: HealthStatus;
}

export interface TodayItem {
  rank: number;
  title: string;
  url?: string;
  raw_metric?: string | number;
  subject_type: string;
  metadata: Record<string, unknown>;
}

export interface TodayList {
  source_key: string;
  source: string;
  list_name: string;
  observed_at?: string;
  status: "ready" | "unavailable" | "not_configured";
  partial: boolean;
  metric_label: string;
  content_type: "ranking" | "market_pulse";
  items: TodayItem[];
}

export interface TodayDomain {
  domain: "computer" | "finance" | "gaming";
  lists: TodayList[];
}

export interface TodayResponse {
  generated_at: string;
  domains: TodayDomain[];
  finance_news: NewsSource[];
}

export interface NewsItem {
  id: string; title: string; url: string; source: string; domain: string;
  content_type: "news" | "feed"; language: string; published_at?: string;
  observed_at: string; linked_subjects: Array<{id: string; canonical_name: string}>;
}
export interface NewsSource {
  source_key: string; source: string; content_type: "news" | "feed";
  status: string; items: NewsItem[];
}

export interface TrendingSource {
  source: string; source_key: string; primary_language: string; rank_list: string;
  source_rank: number; weight: number; freshness: number; contribution: number;
  snapshot_time: string; age_hours: number;
}
export interface TrendingItem {
  rank: number; subject_id: string; canonical_name: string; subject_type: string;
  localized_names: Record<string, string>; final_score: number; source_count: number;
  language_coverage: "chinese" | "international" | "both"; previous_rank?: number;
  rank_change?: number; trend: "up" | "down" | "unchanged" | "new";
  sources: TrendingSource[];
  related_news_count: number;
  related_news_preview: NewsItem[];
}
export interface TrendingResponse {
  generated_at: string;
  domains: Array<{domain: "computer" | "finance" | "gaming"; status: string;
    calculated_at?: string; ranking_run_id?: string; items: TrendingItem[]}>;
}

/**
 * The browser-visible backend configuration has one source of truth. Keep the
 * URL out of page components so deployments only need to change the env var.
 */
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/+$/, "");

const healthEndpoints = {
  backend: "/health",
  database: "/health/db",
} as const;

type HealthEndpoint = keyof typeof healthEndpoints;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function errorStatus(detail: string): HealthStatus {
  return { state: "error", detail };
}

function endpointUrl(endpoint: HealthEndpoint): string | undefined {
  if (!API_BASE_URL) {
    return undefined;
  }

  return `${API_BASE_URL}${healthEndpoints[endpoint]}`;
}

async function requestHealth(endpoint: HealthEndpoint): Promise<HealthStatus> {
  const url = endpointUrl(endpoint);

  if (!url) {
    return errorStatus("NEXT_PUBLIC_API_BASE_URL is not configured.");
  }

  try {
    const response = await fetch(url, { cache: "no-store" });
    const payload: unknown = await response.json().catch(() => null);

    if (!response.ok) {
      const detail = isRecord(payload) ? readString(payload.detail) : undefined;
      return errorStatus(detail ?? `Request failed (HTTP ${response.status}).`);
    }

    if (!isRecord(payload)) {
      return errorStatus("The health endpoint returned an invalid response.");
    }

    const status = readString(payload.status);
    if (status?.toLowerCase() !== "ok") {
      return errorStatus(status ? `Service status: ${status}.` : "Response has no status field.");
    }

    if (endpoint === "database") {
      const database = readString(payload.database);
      if (database?.toLowerCase() !== "connected") {
        return errorStatus(
          database ? `Database status: ${database}.` : "Response has no database status field.",
        );
      }

      return { state: "healthy", detail: "Connected" };
    }

    return { state: "healthy", detail: "Available" };
  } catch (error) {
    return errorStatus(error instanceof Error ? error.message : "Network request failed.");
  }
}

export async function checkPlatformHealth(): Promise<PlatformHealth> {
  const [backend, database] = await Promise.all([
    requestHealth("backend"),
    requestHealth("database"),
  ]);

  return { backend, database };
}

export async function fetchToday(signal?: AbortSignal): Promise<TodayResponse> {
  if (!API_BASE_URL) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is not configured.");
  }
  const response = await fetch(`${API_BASE_URL}/api/v1/today`, { cache: "no-store", signal });
  if (!response.ok) {
    throw new Error(`Today API failed (HTTP ${response.status}).`);
  }
  return (await response.json()) as TodayResponse;
}

export async function fetchTrending(signal?: AbortSignal): Promise<TrendingResponse> {
  if (!API_BASE_URL) throw new Error("NEXT_PUBLIC_API_BASE_URL is not configured.");
  const response = await fetch(`${API_BASE_URL}/api/v1/trending`, { cache: "no-store", signal });
  if (!response.ok) throw new Error(`Trending API failed (HTTP ${response.status}).`);
  return (await response.json()) as TrendingResponse;
}
