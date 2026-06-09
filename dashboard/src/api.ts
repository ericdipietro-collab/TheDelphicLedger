// API client — all calls go to /api/ (proxied to FastAPI in dev)

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(detail.detail ?? res.statusText)
  }
  return res.json() as Promise<T>
}

// ── Types ──────────────────────────────────────────────────────────────────────

export interface RunSummary {
  run_id: string
  run_at: string
  oracle_count: number
  proposal_count: number
  scenario_id: string | null
  regime_state: string | null
}

export interface HoldingScoreOut {
  instrument_id: number
  score: number | null
  reasons: string[]
}

export interface OracleCard {
  oracle_id: string
  display_name: string
  scored_count: number
  abstained: boolean
  abstain_reason: string | null
  sleeve_targets: Record<string, string>
  top_scores: HoldingScoreOut[]
  regime_state: string | null
  proposal_count: number
}

export interface DissentCell {
  oracle_id: string
  direction: string | null
  qty: string | null
  score: number | null
}

export interface DissentRow {
  instrument_id: number
  ticker: string | null
  name: string | null
  cells: DissentCell[]
}

export interface RivalObjection {
  oracle_id: string
  rival_id: string
  instrument_id: number
  ticker: string | null
  oracle_direction: string
  rival_direction: string
}

export interface ChamberResponse {
  run_id: string
  run_at: string
  scenario_id: string | null
  oracle_cards: OracleCard[]
  dissent_matrix: DissentRow[]
  rival_objections: RivalObjection[]
}

export interface SleeveAllocation {
  sleeve: string
  market_value: string
  weight: number
}

export interface DriftGauge {
  sleeve: string
  actual_weight: number
  target_weight: number
  drift_abs: number
  outside_band: boolean
}

export interface HoldingRow {
  instrument_id: number
  ticker: string | null
  name: string | null
  instrument_type: string | null
  sleeve: string | null
  account_id: string | null
  qty: string
  market_value: string
  oracle_scores: Record<string, number | null>
  has_8k_flag: boolean
}

export interface PortfolioResponse {
  as_of: string
  total_market_value: string
  allocations: SleeveAllocation[]
  drift_gauges: DriftGauge[]
  holdings: HoldingRow[]
  selected_oracle: string
}

export interface ProposalOut {
  instrument_id: number
  ticker: string | null
  name: string | null
  account_id: string
  direction: string
  qty: string
  estimated_value: string
  rationale_tags: string[]
  oracle_score: number | null
  tax_note: string | null
}

export interface OracleProposals {
  oracle_id: string
  display_name: string
  proposals: ProposalOut[]
}

export interface TradesResponse {
  run_id: string
  constraint: string | null
  oracle_proposals: OracleProposals[]
}

export interface PackSummary {
  pack_id: string
  display_name: string
  pack_type: string
  has_run: boolean
}

export interface SleeveWaterfall {
  sleeve: string
  before_mv: string
  shock_pct: number
  after_mv: string
  delta_mv: string
}

export interface OracleVerdictDelta {
  oracle_id: string
  display_name: string
  scored_count_baseline: number
  scored_count_scenario: number
  abstained_baseline: boolean
  abstained_scenario: boolean
}

export interface ScenarioResult {
  pack_id: string
  display_name: string
  pack_type: string
  run_id: string
  run_at: string
  waterfall: SleeveWaterfall[]
  verdict_deltas: OracleVerdictDelta[]
  honesty_note: string | null
}

export interface ReconBreakOut {
  id: number
  ticker: string | null
  account_id: string
  as_of: string
  expected_qty: string | null
  actual_qty: string | null
  delta: string | null
  status: string
  coverage_gap: boolean
  suggested_cause: string | null
  resolution_note: string | null
}

export interface ReconResponse {
  open_count: number
  coverage_gap_count: number
  resolved_count: number
  breaks: ReconBreakOut[]
}

export interface ConfigResponse {
  drift_abs: number
  drift_rel: number
  min_trade_usd: number
  new_money: number
  available_profiles: string[]
}

export interface BundleInfo {
  id: string
  display_name: string
  enabled: boolean
  instrument_count: number
  last_refreshed_at: string | null
}

export interface RecomputeRequest {
  run_id?: string
  constraint: string
  drift_abs: number
  drift_rel: number
  min_trade_usd: number
  new_money: number
}

// ── API calls ──────────────────────────────────────────────────────────────────

export const api = {
  listRuns: () => get<RunSummary[]>('/runs'),
  getLatestChamber: () => get<ChamberResponse>('/runs/latest'),
  getChamber: (run_id: string) => get<ChamberResponse>(`/runs/${run_id}`),
  getPortfolio: (oracle?: string) => get<PortfolioResponse>(`/portfolio${oracle ? `?oracle=${oracle}` : ''}`),
  getTrades: (run_id?: string) => get<TradesResponse>(`/trades${run_id ? `?run_id=${run_id}` : ''}`),
  recomputeTrades: (body: RecomputeRequest) => post<TradesResponse>('/trades/recompute', body),
  listScenarios: () => get<PackSummary[]>('/scenarios'),
  getScenario: (pack_id: string) => get<ScenarioResult>(`/scenarios/${pack_id}`),
  getRecon: () => get<ReconResponse>('/recon'),
  getConfig: () => get<ConfigResponse>('/config'),
  getBundles: () => get<BundleInfo[]>('/config/bundles'),
  setBundleEnabled: (id: string, enabled: boolean) =>
    put<BundleInfo>(`/config/bundles/${id}`, { enabled }),
  convene: (constraint = 'unconstrained') => post<ChamberResponse>('/actions/convene', { constraint }),
  runScenario: (pack_id: string, constraint = 'unconstrained') =>
    post<ScenarioResult>(`/actions/scenario/${pack_id}/run`, { constraint }),
}
