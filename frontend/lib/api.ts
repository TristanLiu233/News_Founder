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
  domain: Domain;
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
export type Domain = "computer" | "finance" | "gaming";
export type Trend = "up" | "down" | "unchanged" | "new";
export type LanguageCoverage = "chinese" | "international" | "both";
/** Display names resolved by the backend's shared alias rule, keyed by UI locale. */
export type DisplayNames = Record<"en" | "zh-CN", string>;

export interface TrendingItem {
  rank: number; subject_id: string; canonical_name: string; subject_type: string;
  localized_names: Record<string, string>; display_names: DisplayNames;
  final_score: number; source_count: number;
  language_coverage: LanguageCoverage; previous_rank?: number;
  rank_change?: number; trend: Trend;
  sources: TrendingSource[];
  related_news_count: number;
  related_news_preview: NewsItem[];
}
export interface TrendingDomain {
  domain: Domain; status: "ready" | "unavailable";
  calculated_at?: string; ranking_run_id?: string; total: number; items: TrendingItem[];
}
export interface TrendingResponse {
  generated_at: string;
  domains: TrendingDomain[];
}

export interface NewsPage {
  items: NewsItem[]; limit: number; offset: number;
}

export interface SourceInfo {
  key: string; name: string; domain: Domain; enabled: boolean; configured: boolean;
  latest_observed_at: string | null; latest_item_count: number;
  content_type: "ranking" | "market_pulse" | "news" | "feed";
  /** True when the key is accepted by `/api/v1/search?source=`. */
  searchable: boolean;
}

export interface SearchResult {
  subject_id: string; display_name: string; canonical_name: string; domain: Domain;
  subject_type: string; matched_on: string; matched_alias: string | null; recent_title: string | null;
  latest_sources: {source: string; latest_rank: number; latest_observed_at: string}[];
}
export interface SearchResponse {
  items: SearchResult[]; total: number; offset: number; limit: number;
  mode_used: string; semantic_search_available: boolean;
}

export interface SubjectAliasInfo {
  alias: string; locale: string;
  alias_type: "official_name" | "localized_name" | "source_name" | string;
  alias_source: string;
}
export interface SubjectCurrentRanking {
  ranking_run_id: string; calculated_at: string; rank: number; final_score: number;
  source_count: number; best_source_rank: number; previous_rank: number | null;
  rank_change: number | null; trend: Trend; language_coverage: LanguageCoverage;
}
export interface SourceAppearance {
  source: string; source_key: string | null; metric_label: string | null;
  primary_language: string; rank_list: string; list_type: "ranking" | "market_pulse";
  in_current_snapshot: boolean; latest_rank: number; latest_observed_at: string;
  title: string | null; url: string | null; raw_metric: unknown;
  appearance_count: number; best_rank: number; first_seen_at: string;
}
export interface SubjectDetail {
  subject_id: string; requested_subject_id: string; redirected: boolean;
  canonical_name: string; display_names: DisplayNames; domain: Domain; subject_type: string;
  description: string | null; aliases: SubjectAliasInfo[];
  current_ranking: SubjectCurrentRanking | null;
  source_appearances: SourceAppearance[]; source_appearance_count: number;
  observation_count: number; latest_observed_at: string | null; related_news_count: number;
}
export interface RankHistoryPoint {
  ranking_run_id: string; calculated_at: string; rank: number; final_score: number;
  source_count: number; trend: Trend | null; rank_change: number | null;
}
export interface SubjectHistory {
  subject_id: string; requested_subject_id: string; domain: Domain;
  limit: number; total: number; items: RankHistoryPoint[];
}

/** External company rankings (Phase 4): publisher rankings shown as published, never a platform score. */
export type CompanyMovement = "up" | "down" | "unchanged" | "new";
export interface CompanyRankingDefinition {
  /** Null while a registered ranking has never been ingested. */
  id: string | null; key: string; name: string; publisher: string; source_key: string; domain: Domain;
  methodology: string | null; metric_type: string; metric_label: string; metric_unit: string | null;
  frequency: string; acquisition: string; primary_language: string; source_url: string | null;
}
export interface CompanyRankingSnapshotInfo {
  id: string; snapshot_key: string; observed_at: string; published_at: string | null; effective_at: string;
  source_url: string; status: "complete" | "partial"; entry_count: number; source_entry_count: number | null;
  metadata: {basis?: string; metric_header?: string; stored_limit?: number; revision_id?: number; [key: string]: unknown};
}
export interface CompanyRankingSummary extends CompanyRankingDefinition {
  status: "ready" | "no_snapshot"; snapshot_count: number; latest_snapshot: CompanyRankingSnapshotInfo | null;
}
export interface CompanyRankingsPage {
  items: CompanyRankingSummary[]; total: number; limit: number; offset: number;
}
export interface CompanyRankingEntry {
  company_id: string; canonical_name: string; display_name: string; rank: number;
  previous_rank: number | null; rank_change: number | null;
  /** Null for the first tracked snapshot, where no movement can be known. */
  movement: CompanyMovement | null; is_new: boolean;
  raw_value: string | null; metric_value: number | null; metadata: Record<string, unknown>;
}
export interface CompanyRankingDetail {
  ranking: CompanyRankingDefinition; status: "ready" | "no_snapshot"; snapshot_count: number;
  snapshot: CompanyRankingSnapshotInfo | null; previous_snapshot: CompanyRankingSnapshotInfo | null;
  entries: CompanyRankingEntry[]; total: number; limit: number; offset: number;
  dropped: {company_id: string; display_name: string; previous_rank: number}[]; dropped_count: number;
}
export interface CompanyRankingHistoryItem extends CompanyRankingSnapshotInfo {
  leader: {company_id: string; name: string} | null;
}
export interface CompanyRankingHistory {
  ranking: CompanyRankingDefinition; items: CompanyRankingHistoryItem[];
  total: number; limit: number; offset: number;
}
export interface CompanyRankingPoint {
  snapshot_id: string; effective_at: string; observed_at: string; rank: number | null;
  previous_rank: number | null; rank_change: number | null;
  movement: CompanyMovement | "dropped" | null; raw_value: string | null;
}
export interface CompanyRankingAppearances {
  company_id: string; canonical_name: string;
  aliases: {alias: string; alias_type: string; alias_source: string}[];
  identities: {scheme: string; value: string; source_key: string}[];
  rankings: {ranking: CompanyRankingDefinition; in_latest_snapshot: boolean; latest_rank: number | null; history: CompanyRankingPoint[]}[];
}

/** Persisted Company Intelligence read models (Phase 5); separate from Subject search. */
export interface CompanyAliasInfo {
  alias: string; locale: string | null; alias_type: string; alias_source: string;
}
export interface CompanyIdentityInfo {
  scheme: string; value: string; source_key: string;
}
export interface CompanyListRanking {
  key: string; name: string; domain: Domain; rank: number;
}
export interface CompanyListItem {
  id: string; canonical_name: string;
  names: {en: string; zh: string | null; zh_basis: string | null};
  display_names: DisplayNames; has_profile: boolean; country: string | null;
  industry: string | null; categories: string[]; rankings: CompanyListRanking[];
}
export interface CompaniesPage {
  items: CompanyListItem[]; total: number; limit: number; offset: number;
}
export interface CompanyProfile {
  legal_name: string | null; legal_name_locale: string | null; website: string | null;
  country: string | null; headquarters: string | null; jurisdiction: string | null;
  industry: string | null; categories: string[]; description: string | null;
  description_language: string | null; business_areas: string[]; key_products: string[];
  field_sources: Record<string, string>; profile_updated_at: string;
}
export interface CompanyProfileSource {
  source_key: string; source_type: string; source_name: string; source_url: string;
  retrieved_at: string; checked_at: string; published_at: string | null;
  fields: string[]; used_fields: string[]; metadata: Record<string, unknown>;
}
export interface CompanyCurrentRanking {
  ranking: CompanyRankingDefinition; in_latest_snapshot: boolean; latest_rank: number | null;
  movement: CompanyMovement | "dropped" | null; rank_change: number | null;
  raw_value: string | null; effective_at: string | null;
}
export interface CompanyRelatedSubject {
  id: string; title: string; domain: Domain; subject_type: string;
  strategy: string; evidence: Record<string, unknown>;
}
export interface CompanyDetail {
  id: string; requested_id: string; redirected: boolean; canonical_name: string;
  names: {en: string; zh: string | null; zh_basis: string | null};
  display_names: DisplayNames; aliases: CompanyAliasInfo[]; identities: CompanyIdentityInfo[];
  profile: CompanyProfile | null; profile_sources: CompanyProfileSource[];
  current_rankings: CompanyCurrentRanking[]; domains: Domain[];
  related_subjects: CompanyRelatedSubject[]; related_news_count: number;
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

/** Carries the HTTP status so pages can distinguish "not found" from "unavailable". */
export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

type Query = Record<string, string | number | boolean | undefined>;

async function getJson<T>(path: string, query: Query = {}, signal?: AbortSignal): Promise<T> {
  if (!API_BASE_URL) throw new Error("NEXT_PUBLIC_API_BASE_URL is not configured.");
  const params = new URLSearchParams();
  Object.entries(query).forEach(([key, value]) => { if (value !== undefined) params.set(key, String(value)); });
  const search = params.toString();
  const response = await fetch(`${API_BASE_URL}${path}${search ? `?${search}` : ""}`, { cache: "no-store", signal });
  if (!response.ok) throw new ApiError(`${path} failed (HTTP ${response.status}).`, response.status);
  return (await response.json()) as T;
}

export function fetchToday(signal?: AbortSignal): Promise<TodayResponse> {
  return getJson("/api/v1/today", {}, signal);
}

export function fetchTrending(
  signal?: AbortSignal, options: {domain?: Domain; limit?: number} = {},
): Promise<TrendingResponse> {
  return getJson("/api/v1/trending", options, signal);
}

export function fetchNews(
  options: {domain?: Domain; source?: string; subject_id?: string; company_id?: string; limit?: number; offset?: number},
  signal?: AbortSignal,
): Promise<NewsPage> {
  return getJson("/api/v1/news", options, signal);
}

export async function fetchSources(signal?: AbortSignal): Promise<SourceInfo[]> {
  return (await getJson<{sources: SourceInfo[]}>("/api/v1/sources", {}, signal)).sources;
}

export function fetchSearch(params: URLSearchParams, signal?: AbortSignal): Promise<SearchResponse> {
  return getJson("/api/v1/search", Object.fromEntries(params), signal);
}

export function fetchSubject(id: string, signal?: AbortSignal): Promise<SubjectDetail> {
  return getJson(`/api/v1/subjects/${encodeURIComponent(id)}`, {}, signal);
}

export function fetchSubjectHistory(id: string, limit: number, signal?: AbortSignal): Promise<SubjectHistory> {
  return getJson(`/api/v1/subjects/${encodeURIComponent(id)}/history`, {limit}, signal);
}

export function fetchCompanyRankings(
  options: {domain?: Domain; source?: string; limit?: number; offset?: number}, signal?: AbortSignal,
): Promise<CompanyRankingsPage> {
  return getJson("/api/v1/company-rankings", options, signal);
}

export function fetchCompanyRanking(
  ref: string, options: {snapshot_id?: string; limit?: number; offset?: number}, signal?: AbortSignal,
): Promise<CompanyRankingDetail> {
  return getJson(`/api/v1/company-rankings/${encodeURIComponent(ref)}`, options, signal);
}

export function fetchCompanyRankingHistory(ref: string, limit: number, signal?: AbortSignal): Promise<CompanyRankingHistory> {
  return getJson(`/api/v1/company-rankings/${encodeURIComponent(ref)}/history`, {limit}, signal);
}

export function fetchCompanyAppearances(companyId: string, limit: number, signal?: AbortSignal): Promise<CompanyRankingAppearances> {
  return getJson(`/api/v1/companies/${encodeURIComponent(companyId)}/rankings`, {limit}, signal);
}

export function fetchCompanies(
  options: {q?: string; domain?: Domain; ranking?: string; has_profile?: boolean; limit?: number; offset?: number},
  signal?: AbortSignal,
): Promise<CompaniesPage> {
  return getJson("/api/v1/companies", options, signal);
}

export function fetchCompany(id: string, signal?: AbortSignal): Promise<CompanyDetail> {
  return getJson(`/api/v1/companies/${encodeURIComponent(id)}`, {}, signal);
}
