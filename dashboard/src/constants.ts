export const ORACLE_IDS = [
  'value_purist',
  'growth_visionary',
  'yield_harvester',
  'macro_tactician',
  'quality_compounder',
  'passive_pragmatist',
] as const

export const ORACLE_DISPLAY: Record<string, string> = {
  value_purist:       'Value Purist',
  growth_visionary:   'Growth Visionary',
  yield_harvester:    'Yield Harvester',
  macro_tactician:    'Macro Tactician',
  quality_compounder: 'Quality Compounder',
  passive_pragmatist: 'Passive Pragmatist',
}

export const ORACLE_SHORT: Record<string, string> = {
  value_purist:       'Value',
  growth_visionary:   'Growth',
  yield_harvester:    'Yield',
  macro_tactician:    'Macro',
  quality_compounder: 'Quality',
  passive_pragmatist: 'Passive',
}

export const ORACLE_COLOR: Record<string, string> = {
  value_purist:       '#f59e0b',
  growth_visionary:   '#10b981',
  yield_harvester:    '#0ea5e9',
  macro_tactician:    '#8b5cf6',
  quality_compounder: '#06b6d4',
  passive_pragmatist: '#64748b',
}

export const SLEEVE_LABELS: Record<string, string> = {
  equity_us:     'US Equity',
  equity_intl:   'Intl Equity',
  fixed_income:  'Fixed Income',
  alternatives:  'Alternatives',
  cash:          'Cash',
}

export const SLEEVE_COLORS = [
  '#3b82f6',  // blue-500
  '#22c55e',  // green-500
  '#f97316',  // orange-500
  '#a855f7',  // purple-500
  '#64748b',  // slate-500
]

export const DIRECTION_COLOR: Record<string, string> = {
  buy:  '#10b981',
  sell: '#f43f5e',
  hold: '#64748b',
}

/** Display-only: converts decimal string to USD currency label. parseFloat acceptable here — display boundary only. */
export function fmtMoney(s: string | null | undefined): string {
  if (!s || s === 'None') return 'n/a'
  const n = parseFloat(s)
  if (isNaN(n)) return 'n/a'
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(n)
}

export function fmtPct(n: number | null | undefined, decimals = 1): string {
  if (n == null) return 'n/a'
  return `${(n * 100).toFixed(decimals)}%`
}

export function fmtScore(n: number | null | undefined): string {
  if (n == null) return '—'
  return n.toFixed(2)
}

export function scoreColor(n: number | null | undefined): string {
  if (n == null) return '#64748b'
  if (n > 0.3) return '#10b981'
  if (n < -0.3) return '#f43f5e'
  return '#f59e0b'
}
