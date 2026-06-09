import { useEffect, useState } from 'react'
import {
  ShieldCheck, Rocket, Banknote, Globe, Cpu, LayoutGrid,
  AlertTriangle, RefreshCw, TrendingUp, BarChart2, FileText, Tag, Boxes, Info,
  CheckCircle2, Circle, ArrowRight,
} from 'lucide-react'
import { api, BundleInfo, OracleCard, ChamberResponse, DissentRow, RivalObjection, SetupStatus } from '../api'
import { ORACLE_IDS, ORACLE_COLOR, ORACLE_DISPLAY, fmtScore, fmtPct, scoreColor, DIRECTION_COLOR } from '../constants'

function daysAgo(isoDate: string): string {
  const ms = Date.now() - new Date(isoDate).getTime()
  const days = Math.floor(ms / 86_400_000)
  if (days === 0) return 'today'
  if (days === 1) return '1 day ago'
  return `${days} days ago`
}

// ── Per-oracle identity metadata ──────────────────────────────────────────────

const ORACLE_ICON = {
  value_purist:       ShieldCheck,
  growth_visionary:   Rocket,
  yield_harvester:    Banknote,
  macro_tactician:    Globe,
  quant:              Cpu,
  passive_pragmatist: LayoutGrid,
} as Record<string, React.ElementType>

const ORACLE_PHILOSOPHY = {
  value_purist: {
    tagline: 'Margin of safety',
    metrics: 'P/E · P/B · FCF · D/E',
    description: 'Seeks companies trading meaningfully below their intrinsic value. Inspired by Graham and Buffett: only buys when price leaves a cushion against estimation error. Scores heavily on P/E, P/B, free cash flow yield, and debt discipline. Will not chase momentum or pay a growth premium — patience is the edge.',
    goal: 'Buy undervalued, avoid overpaying',
  },
  growth_visionary: {
    tagline: 'Category winners',
    metrics: 'Rev YoY · Margin · Mom.',
    description: 'Identifies durable compounders with accelerating revenue, expanding margins, and strong price momentum. Willing to pay up for companies that can sustain above-market growth for years. Scores on revenue YoY, gross margin trajectory, and 6-month momentum. Avoids value traps and slow-growth industries.',
    goal: 'Own the winners before consensus catches up',
  },
  yield_harvester: {
    tagline: 'The check clears',
    metrics: 'Distribution · Payout',
    description: 'Income first. Prioritises assets where dividends and distributions are well-covered and growing. Scores on trailing distribution yield, payout sustainability, and multi-year dividend growth streaks. Abstains when income data is absent. Skews toward funds, REITs, and dividend-growth equities.',
    goal: 'Generate reliable, growing cash income',
  },
  macro_tactician: {
    tagline: 'Regime-aware ballast',
    metrics: 'T10Y3M · CPI · DXY · VIX',
    description: 'Reads the macro environment — yield curve shape, inflation regime, dollar strength, and volatility — to determine portfolio tilt. Does not score individual securities. Instead sets sleeve targets (equity vs. fixed income vs. alternatives) based on whether the regime is neutral, defensive, or aggressive. Uses hysteresis to avoid flip-flopping on noise.',
    goal: 'Right-size risk for the current macro regime',
  },
  quant: {
    tagline: 'Humans are biased',
    metrics: 'RSI · MA · 12-1 · β',
    description: 'Systematic and signal-driven — ignores narrative entirely. Scores on technical price signals: RSI(14), 50/200-day MA cross, 12-month-minus-1-month momentum factor, and beta to SPY. Assumes market participants exhibit predictable behavioral biases that create exploitable patterns in price data.',
    goal: 'Capture systematic price-based factors',
  },
  passive_pragmatist: {
    tagline: 'Cost & concentration',
    metrics: 'Expense · HHI · Turnover',
    description: 'The cost-conscious diversifier. Strongly prefers low-expense broad index funds over individual stocks. Scores on expense ratio (lower is better), portfolio concentration via HHI, and implied turnover. Will only propose buying funds — never individual equities. The counterweight to the stock-picking oracles.',
    goal: 'Minimise cost and concentration drag',
  },
} as Record<string, { tagline: string; metrics: string; description: string; goal: string }>

// Strip leading "The " so "The Quant" → "Quant" in compact contexts
const ORACLE_SHORT = Object.fromEntries(
  Object.entries(ORACLE_DISPLAY).map(([id, name]) => [id, name.replace(/^The\s+/, '').split(' ')[0]])
) as Record<string, string>

// ── Oracle card ───────────────────────────────────────────────────────────────

function OracleCardView({ card }: { card: OracleCard }) {
  const color = ORACLE_COLOR[card.oracle_id] ?? '#64748b'
  const Icon = ORACLE_ICON[card.oracle_id] ?? LayoutGrid
  const meta = ORACLE_PHILOSOPHY[card.oracle_id]

  return (
    <article
      className="rounded-2xl overflow-hidden flex flex-col bg-slate-900"
      style={{ border: `1px solid ${color}33` }}
    >
      {/* Identity header — gradient-tinted, icon tile + name */}
      <header
        className="flex items-center gap-3 p-4"
        style={{
          background: `linear-gradient(180deg, ${color}1f 0%, transparent 100%)`,
          borderBottom: `1px solid ${color}22`,
        }}
      >
        <div
          className="flex-shrink-0 w-10 h-10 grid place-items-center rounded-xl"
          style={{ color, background: `${color}26`, border: `1px solid ${color}4d` }}
        >
          <Icon size={20} />
        </div>
        <div className="flex-1 min-w-0">
          <p
            className="font-mono text-[10px] tracking-[0.17em] uppercase mb-0.5"
            style={{ color }}
          >
            {card.oracle_id}
          </p>
          <h3 className="text-[15px] font-bold text-slate-100 leading-tight truncate">
            {card.display_name}
          </h3>
        </div>

        {/* Info tooltip */}
        {meta && (
          <div className="relative flex-shrink-0 group/tooltip">
            <button
              className="w-6 h-6 grid place-items-center rounded-md text-slate-600 hover:text-slate-400 transition-colors"
              aria-label="Oracle philosophy"
            >
              <Info size={13} />
            </button>
            <div
              className="absolute right-0 top-8 z-20 w-72 rounded-xl p-3.5 text-xs leading-relaxed
                         opacity-0 pointer-events-none group-hover/tooltip:opacity-100 group-hover/tooltip:pointer-events-auto
                         transition-opacity duration-150"
              style={{
                background: '#0f172a',
                border: `1px solid ${color}44`,
                boxShadow: `0 8px 32px #00000080, 0 0 0 1px ${color}22`,
              }}
            >
              <p className="font-semibold text-slate-200 mb-1" style={{ color }}>
                {meta.goal}
              </p>
              <p className="text-slate-400 mb-2">{meta.description}</p>
              <p className="text-slate-600 font-mono text-[10px] uppercase tracking-wider">
                Signals: {meta.metrics}
              </p>
            </div>
          </div>
        )}

        {card.abstained ? (
          <span
            className="flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-mono"
            style={{ background: '#64748b22', color: '#94a3b8', border: '1px solid #64748b44' }}
          >
            Abstain
          </span>
        ) : (
          <span
            className="flex-shrink-0 text-xs px-2 py-0.5 rounded-full font-mono"
            style={{ background: `${color}22`, color: `${color}cc`, border: `1px solid ${color}44` }}
          >
            {card.scored_count} Scored
          </span>
        )}
      </header>

      <div className="p-4 flex-1 space-y-3">
        {/* Philosophy + metrics tagline */}
        {meta && (
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400 italic">{meta.tagline}</span>
            <span className="text-slate-600 font-mono text-[10px]">{meta.metrics}</span>
          </div>
        )}

        {card.regime_state && (
          <div
            className="px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-2"
            style={{ background: `${color}10`, border: `1px solid ${color}20` }}
          >
            <span className="text-slate-500">Regime</span>
            <span className="font-mono font-medium" style={{ color }}>{card.regime_state}</span>
          </div>
        )}

        {/* Abstain panel or conviction list */}
        {card.abstained ? (
          <div
            className="rounded-lg p-3 text-xs space-y-1.5"
            style={{ background: '#0f172a', border: '1px solid #1e293b' }}
          >
            <p className="text-slate-400 font-medium">Recusing — insufficient data</p>
            {card.abstain_reason && (
              <p className="text-slate-600 leading-relaxed">{card.abstain_reason}</p>
            )}
          </div>
        ) : (
          <div className="space-y-2">
            {card.top_scores.length === 0 ? (
              <p className="text-xs text-slate-600 italic">No scores available.</p>
            ) : (
              card.top_scores.map(hs => (
                <div key={hs.instrument_id} className="space-y-0.5">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs text-slate-400 font-mono w-14 flex-shrink-0">
                      {hs.ticker ?? `#${hs.instrument_id}`}
                    </span>
                    <div className="flex-1 h-1.5 rounded-full bg-slate-950 flex overflow-hidden">
                      <span className="w-1/2 flex justify-end">
                        {(hs.score ?? 0) < 0 && (
                          <i
                            className="block h-full rounded-l"
                            style={{
                              width: `${Math.min(Math.abs(hs.score ?? 0) * 100, 100)}%`,
                              background: scoreColor(hs.score),
                            }}
                          />
                        )}
                      </span>
                      <span className="w-1/2 flex">
                        {(hs.score ?? 0) > 0 && (
                          <i
                            className="block h-full rounded-r"
                            style={{
                              width: `${Math.min((hs.score ?? 0) * 100, 100)}%`,
                              background: scoreColor(hs.score),
                            }}
                          />
                        )}
                      </span>
                    </div>
                    <span
                      className="text-xs font-mono tabular w-10 text-right flex-shrink-0"
                      style={{ color: scoreColor(hs.score) }}
                    >
                      {fmtScore(hs.score)}
                    </span>
                  </div>
                  {hs.reasons[0] && (
                    <p className="text-[10px] text-slate-600 pl-16 leading-tight truncate">
                      {hs.reasons[0]}
                    </p>
                  )}
                </div>
              ))
            )}
          </div>
        )}

        {/* Sleeve targets */}
        <div className="pt-2.5 border-t border-slate-800/60">
          <p className="text-[10px] text-slate-600 mb-1.5 uppercase tracking-wider font-medium">
            Sleeve targets
          </p>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            {Object.entries(card.sleeve_targets).map(([sleeve, target]) => (
              <div key={sleeve} className="flex justify-between text-xs">
                <span className="text-slate-500 truncate">{sleeve.replace(/_/g, ' ')}</span>
                <span className="font-mono text-slate-300 tabular">{fmtPct(parseFloat(target))}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="text-[10px] text-slate-600 font-mono">{card.proposal_count} proposals</div>
      </div>
    </article>
  )
}

// ── Dissent matrix ────────────────────────────────────────────────────────────

function isSplitRow(cells: DissentRow['cells']): boolean {
  const dirs = cells.map(c => c.direction).filter(Boolean)
  return dirs.includes('buy') && dirs.includes('sell')
}

function DissentMatrix({ rows, oracleAbstained }: { rows: DissentRow[]; oracleAbstained: Set<string> }) {
  const oracles = ORACLE_IDS
  const [filter, setFilter] = useState<'all' | 'splits'>('splits')

  if (rows.length === 0) {
    return <p className="text-slate-600 text-sm">No oracle scores yet — run Re-convene to populate.</p>
  }

  const splitCount = rows.filter(r => isSplitRow(r.cells)).length
  const filtered = filter === 'splits' ? rows.filter(r => isSplitRow(r.cells)) : rows

  return (
    <div>
      <div className="flex items-center gap-2 mb-3 text-xs">
        {(['splits', 'all'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1 rounded-full border transition-colors ${
              filter === f
                ? 'bg-slate-700 border-slate-600 text-slate-100'
                : 'bg-transparent border-slate-800 text-slate-500 hover:border-slate-600 hover:text-slate-400'
            }`}
          >
            {f === 'splits' ? `Splits (${splitCount})` : `All (${rows.length})`}
          </button>
        ))}
      </div>
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            <th
              className="text-left px-4 py-3 text-slate-500 font-medium w-40"
              style={{ borderRight: '1px solid #1e293b', borderBottom: '1px solid #1e293b' }}
            >
              Ticker
            </th>
            {oracles.map(oid => {
              const color = ORACLE_COLOR[oid]
              const abstained = oracleAbstained.has(oid)
              const Icon = ORACLE_ICON[oid] ?? LayoutGrid
              return (
                <th
                  key={oid}
                  className="px-2 py-0 text-center w-20"
                  style={{ borderRight: '1px solid #1e293b', borderBottom: '1px solid #1e293b' }}
                >
                  {/* Colored top bar per column */}
                  <div
                    className="w-full h-[3px] mb-2"
                    style={{ background: color, opacity: abstained ? 0.3 : 0.85 }}
                  />
                  <div
                    className="flex flex-col items-center gap-0.5 pb-2"
                    style={{ color: abstained ? '#475569' : color }}
                  >
                    <Icon size={13} />
                    <span className="font-mono text-[10px] font-semibold">
                      {ORACLE_SHORT[oid] ?? oid}
                    </span>
                    {abstained && (
                      <span className="text-[9px] text-slate-600 font-mono normal-case">
                        abstain
                      </span>
                    )}
                  </div>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {filtered.map((row, ri) => {
            const split = isSplitRow(row.cells)
            return (
              <tr
                key={row.instrument_id}
                className={`hover:bg-slate-800/30 transition-colors ${
                  ri < filtered.length - 1 ? 'border-b border-slate-800/60' : ''
                } ${split ? 'bg-rose-950/20' : ''}`}
              >
                <td
                  className="px-4 py-2.5"
                  style={{ borderRight: '1px solid #1e293b' }}
                >
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-semibold text-slate-100">
                      {row.ticker ?? `#${row.instrument_id}`}
                    </span>
                    {row.name && (
                      <span className="text-slate-500 text-[10px] truncate hidden sm:block">
                        {row.name.slice(0, 16)}
                      </span>
                    )}
                    {split && (
                      <span className="text-[9px] px-1.5 py-0.5 rounded font-mono font-bold text-rose-400 bg-rose-950/50 border border-rose-900/50 flex-shrink-0">
                        SPLIT
                      </span>
                    )}
                  </div>
                </td>
                {row.cells.map(cell => {
                  const dir = cell.direction ?? 'hold'
                  const color = DIRECTION_COLOR[dir] ?? '#64748b'
                  const isHold = dir === 'hold'
                  const hasScore = cell.score != null
                  return (
                    <td
                      key={cell.oracle_id}
                      className="px-2 py-2.5 text-center"
                      style={{ borderRight: '1px solid #1e293b' }}
                    >
                      {!isHold ? (
                        <span
                          className="inline-block px-2 py-0.5 rounded text-xs font-mono font-bold tracking-wider"
                          style={{
                            color,
                            background: `${color}1a`,
                            border: `1px solid ${color}40`,
                          }}
                        >
                          {dir.toUpperCase()}
                        </span>
                      ) : hasScore ? (
                        <span
                          className="font-mono text-[11px] font-semibold"
                          style={{ color: scoreColor(cell.score) }}
                        >
                          {fmtScore(cell.score)}
                        </span>
                      ) : (
                        <span className="text-slate-700 font-mono">—</span>
                      )}
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
    </div>
  )
}

// ── Rivals' objections ────────────────────────────────────────────────────────

function ObjectionsList({ objections }: { objections: RivalObjection[] }) {
  if (objections.length === 0) {
    return <p className="text-slate-600 text-sm">No direct buy/sell conflicts between rivals.</p>
  }

  return (
    <ul className="space-y-2">
      {objections.map((obj, i) => {
        const colorA = ORACLE_COLOR[obj.oracle_id] ?? '#64748b'
        const colorB = ORACLE_COLOR[obj.rival_id] ?? '#64748b'
        return (
          <li key={i} className="flex flex-wrap items-center gap-2.5 p-3 rounded-lg bg-slate-900 border border-slate-800 text-sm">
            <span className="font-mono font-semibold text-slate-200">
              {obj.ticker ?? `#${obj.instrument_id}`}
            </span>
            <span className="text-slate-700">·</span>
            <span style={{ color: colorA }} className="font-medium">{ORACLE_DISPLAY[obj.oracle_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono font-bold"
              style={{
                color: DIRECTION_COLOR[obj.oracle_direction],
                background: `${DIRECTION_COLOR[obj.oracle_direction]}1a`,
                border: `1px solid ${DIRECTION_COLOR[obj.oracle_direction]}40`,
              }}
            >
              {obj.oracle_direction.toUpperCase()}
            </span>
            <span className="text-slate-600 text-xs">vs</span>
            <span style={{ color: colorB }} className="font-medium">{ORACLE_DISPLAY[obj.rival_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono font-bold"
              style={{
                color: DIRECTION_COLOR[obj.rival_direction],
                background: `${DIRECTION_COLOR[obj.rival_direction]}1a`,
                border: `1px solid ${DIRECTION_COLOR[obj.rival_direction]}40`,
              }}
            >
              {obj.rival_direction.toUpperCase()}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

// ── Setup checklist / empty state ────────────────────────────────────────────

function SetupStep({
  num,
  done,
  label,
  detail,
  action,
  actionLabel,
  running,
  optional,
}: {
  num: number
  done: boolean
  label: string
  detail: string
  action?: () => void
  actionLabel?: string
  running?: boolean
  optional?: boolean
}) {
  return (
    <div className={`flex items-start gap-3 p-3.5 rounded-xl border transition-colors ${
      done
        ? 'bg-emerald-950/30 border-emerald-900/40'
        : 'bg-slate-900 border-slate-800'
    }`}>
      <div className="flex-shrink-0 mt-0.5">
        {done
          ? <CheckCircle2 size={18} className="text-emerald-500" />
          : <Circle size={18} className="text-slate-700" />
        }
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-xs font-mono ${done ? 'text-emerald-600' : 'text-slate-600'}`}>
            {optional ? 'optional' : `step ${num}`}
          </span>
          <span className={`text-sm font-medium ${done ? 'text-emerald-300' : 'text-slate-300'}`}>
            {label}
          </span>
        </div>
        <p className="text-xs text-slate-600 mt-0.5 leading-relaxed">{detail}</p>
      </div>
      {action && !done && (
        <button
          onClick={action}
          disabled={running}
          className="flex-shrink-0 flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-300 disabled:opacity-40"
        >
          {running
            ? <RefreshCw size={11} className="animate-spin" />
            : <ArrowRight size={11} />
          }
          {running ? 'Working…' : actionLabel}
        </button>
      )}
    </div>
  )
}

function EmptyState({
  error,
  onConvene,
  convening,
  onDataOp,
  dataOp,
  setupStatus,
}: {
  error: string | null
  onConvene: () => void
  convening: boolean
  onDataOp: (op: 'prices' | 'macro' | 'edgar' | 'sleeves' | 'bundles') => void
  dataOp: DataOp
  setupStatus: SetupStatus | null
}) {
  const s = setupStatus

  // All required steps done = ready to convene
  const readyToConvene = !!s?.has_holdings && !!s?.has_prices

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] py-12">
      <div className="w-full max-w-lg space-y-6">

        {/* Header */}
        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex items-end gap-1.5 h-10">
            {Object.entries(ORACLE_COLOR).map(([id, color]) => (
              <div
                key={id}
                className="w-2.5 rounded-t"
                style={{ height: `${28 + Math.random() * 12}px`, background: color, opacity: 0.35 }}
              />
            ))}
          </div>
          <h2 className="text-lg font-semibold text-slate-200">Welcome to The Chamber</h2>
          <p className="text-sm text-slate-500 max-w-sm">
            Complete the steps below before convening the oracles.
          </p>
        </div>

        {/* Steps */}
        {s ? (
          <div className="space-y-2">
            {/* Step 1: Import & resolve */}
            <SetupStep
              num={1}
              done={!!s.has_holdings && (s.unresolved_count ?? 0) === 0}
              label="Import & resolve positions"
              detail={
                !s.has_holdings
                  ? 'Upload a positions CSV from Schwab, Fidelity, or Vanguard on the Import page.'
                  : (s.unresolved_count ?? 0) > 0
                    ? `${s.holding_count} holdings loaded · ${s.unresolved_count} unresolved instrument${s.unresolved_count !== 1 ? 's' : ''}`
                    : `${s.holding_count} holdings loaded · all symbols mapped`
              }
              action={
                !s.has_holdings
                  ? () => { window.location.href = '/import' }
                  : (s.unresolved_count ?? 0) > 0
                    ? () => { window.location.href = '/resolve' }
                    : undefined
              }
              actionLabel={
                !s.has_holdings ? 'Go to Import'
                  : (s.unresolved_count ?? 0) > 0 ? 'Resolve →'
                  : undefined
              }
            />

            {/* Step 2: Fetch prices */}
            <SetupStep
              num={2}
              done={!!s.has_prices}
              label="Fetch prices"
              detail={s.has_prices
                ? `${s.price_count.toLocaleString()} price observations loaded`
                : 'Download 380 days of EOD prices for your holdings (required for all oracles).'}
              action={!s.has_prices ? () => onDataOp('prices') : undefined}
              actionLabel="Fetch prices"
              running={dataOp === 'prices'}
            />

            {/* Step 3: Fetch macro */}
            <SetupStep
              num={3}
              done={!!s.has_macro}
              label="Fetch macro data"
              detail={s.has_macro
                ? 'FRED macro series loaded (yield curve, CPI, VIX…)'
                : 'FRED macro signals power the Macro Tactician regime detection.'}
              action={!s.has_macro ? () => onDataOp('macro') : undefined}
              actionLabel="Fetch macro"
              running={dataOp === 'macro'}
              optional
            />

            {/* Step 4: Fetch EDGAR */}
            <SetupStep
              num={4}
              done={!!s.has_edgar}
              label="Fetch fundamentals"
              detail={s.has_edgar
                ? 'EDGAR fundamentals loaded (P/E, revenue growth, FCF…)'
                : 'SEC EDGAR XBRL fundamentals power the Value Purist, Growth Visionary, and Yield Harvester.'}
              action={!s.has_edgar ? () => onDataOp('edgar') : undefined}
              actionLabel="Fetch EDGAR"
              running={dataOp === 'edgar'}
              optional
            />

            {/* Step 5: Universe (optional) */}
            <SetupStep
              num={5}
              done={s.bundles_enabled > 0 && s.universe_size > 0}
              label="Expand buy universe"
              detail={
                s.universe_size > 0
                  ? `${s.universe_size} universe instruments active (${s.bundles_enabled} bundle${s.bundles_enabled !== 1 ? 's' : ''} enabled)`
                  : s.bundles_seeded
                  ? 'Bundles seeded — enable at least one toggle above, then fetch prices again.'
                  : 'Seed bundles to let the rebalancer propose buys beyond your current holdings.'
              }
              action={!s.bundles_seeded ? () => onDataOp('bundles') : undefined}
              actionLabel="Seed bundles"
              running={dataOp === 'bundles'}
              optional
            />
          </div>
        ) : (
          <div className="flex justify-center py-4">
            <RefreshCw size={16} className="animate-spin text-slate-600" />
          </div>
        )}

        {/* Convene button */}
        <div className="flex flex-col items-center gap-3 pt-2">
          <button
            onClick={onConvene}
            disabled={convening || !readyToConvene}
            className="flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-semibold transition-colors disabled:opacity-40
              bg-indigo-600 hover:bg-indigo-500 text-white border border-indigo-500 disabled:cursor-not-allowed"
          >
            <RefreshCw size={14} className={convening ? 'animate-spin' : ''} />
            {convening ? 'Convening…' : 'Convene the oracles'}
          </button>
          {!readyToConvene && s && (
            <p className="text-xs text-slate-600">
              {!s.has_holdings ? 'Import positions first (step 1)' : 'Fetch prices first (step 2)'}
            </p>
          )}
        </div>

        {error && (
          <details className="w-full">
            <summary className="text-xs text-slate-700 cursor-pointer hover:text-slate-500 flex items-center gap-1.5">
              <AlertTriangle size={12} />
              Technical detail
            </summary>
            <p className="mt-2 text-xs text-slate-700 font-mono break-all bg-slate-900 rounded p-2 border border-slate-800">
              {error}
            </p>
          </details>
        )}
      </div>
    </div>
  )
}

// ── Chamber page ──────────────────────────────────────────────────────────────

type DataOp = 'prices' | 'macro' | 'edgar' | 'sleeves' | 'bundles' | null

async function dataPost(path: string): Promise<{ ok: boolean; message: string; count: number }> {
  const res = await fetch(`/api/data/${path}`, { method: 'POST' })
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail ?? res.statusText)
  return body
}

export function Chamber() {
  const [data, setData] = useState<ChamberResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [convening, setConvening] = useState(false)
  const [dataOp, setDataOp] = useState<DataOp>(null)
  const [dataMsg, setDataMsg] = useState<string | null>(null)
  const [constraint, setConstraint] = useState('unconstrained')
  const [profiles, setProfiles] = useState<string[]>(['unconstrained'])
  const [regime, setRegime] = useState<string>('neutral')
  const [regimeSaving, setRegimeSaving] = useState(false)
  const [bundles, setBundles] = useState<BundleInfo[]>([])
  const [bundleToggling, setBundleToggling] = useState<string | null>(null)
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null)

  const refreshSetupStatus = () => api.getSetupStatus().then(setSetupStatus).catch(() => {})

  useEffect(() => {
    api.getLatestChamber().then(setData).catch(e => setError(String(e))).finally(() => setLoading(false))
    fetch('/api/config/regime').then(r => r.json()).then(r => setRegime(r.tilt)).catch(() => {})
    api.getConfig().then(cfg => {
      if (cfg.available_profiles.length > 0) setProfiles(cfg.available_profiles)
    }).catch(() => {})
    api.getBundles().then(setBundles).catch(() => {})
    refreshSetupStatus()
  }, [])

  const handleBundleToggle = async (id: string, enabled: boolean) => {
    setBundleToggling(id)
    try {
      const updated = await api.setBundleEnabled(id, enabled)
      setBundles(prev => prev.map(b => b.id === id ? updated : b))
    } catch (e) {
      setError(String(e))
    } finally {
      setBundleToggling(null)
    }
  }

  const handleConvene = async () => {
    setConvening(true)
    setError(null)
    setDataMsg(null)
    try {
      const result = await api.convene(constraint)
      setData(result)
    } catch (e) {
      setError(String(e))
    } finally {
      setConvening(false)
    }
  }

  const handleDataOp = async (op: 'prices' | 'macro' | 'edgar' | 'sleeves' | 'bundles') => {
    setDataOp(op)
    setDataMsg(null)
    setError(null)
    try {
      const paths: Record<string, string> = {
        prices: 'fetch-prices', macro: 'fetch-macro',
        edgar: 'fetch-edgar', sleeves: 'classify-sleeves',
        bundles: 'seed-bundles',
      }
      const res = await dataPost(paths[op])
      setDataMsg(res.message)
      if (op === 'bundles') api.getBundles().then(setBundles).catch(() => {})
      refreshSetupStatus()
    } catch (e) {
      setError(String(e))
    } finally {
      setDataOp(null)
    }
  }

  if (loading) return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="flex items-end gap-1.5 h-8 animate-pulse">
        {Object.entries(ORACLE_COLOR).map(([id, color]) => (
          <div
            key={id}
            className="w-2 rounded-t"
            style={{ height: `${16 + Math.random() * 16}px`, background: color, opacity: 0.4 }}
          />
        ))}
      </div>
    </div>
  )
  if (!data) return (
    <EmptyState
      error={error}
      onConvene={handleConvene}
      convening={convening}
      onDataOp={handleDataOp}
      dataOp={dataOp}
      setupStatus={setupStatus}
    />
  )

  const abstainedOracles = new Set(
    data.oracle_cards.filter(c => c.abstained).map(c => c.oracle_id)
  )
  const abstainCount = abstainedOracles.size

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold text-white">The Chamber</h1>
          <p className="text-sm text-slate-500 mt-1 font-mono">
            run {data.run_id.slice(0, 8)}… &middot; {new Date(data.run_at).toLocaleString()}
            {data.scenario_id && <span className="ml-2 text-violet-400">scenario: {data.scenario_id}</span>}
          </p>
          {setupStatus && (setupStatus.prices_as_of || setupStatus.edgar_as_of) && (
            <p className="text-xs text-slate-600 font-mono mt-0.5">
              {setupStatus.prices_as_of && (
                <span>Prices: {daysAgo(setupStatus.prices_as_of)}</span>
              )}
              {setupStatus.prices_as_of && setupStatus.edgar_as_of && (
                <span className="mx-2">·</span>
              )}
              {setupStatus.edgar_as_of && (
                <span>EDGAR: {daysAgo(setupStatus.edgar_as_of)}</span>
              )}
            </p>
          )}
        </div>
        {/* Data refresh + convene controls */}
        <div className="flex items-center gap-2 flex-wrap">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-mono">
            <span className="text-slate-300 font-semibold">{data.oracle_cards.length}</span>
            <span className="text-slate-600">oracles</span>
            {abstainCount > 0 && (
              <>
                <span className="text-slate-700">·</span>
                <span className="text-slate-500 font-semibold">{abstainCount}</span>
                <span className="text-slate-600">abstain</span>
              </>
            )}
          </div>
          <button
            onClick={() => handleDataOp('prices')}
            disabled={!!dataOp || convening}
            title="Fetch 90-day prices for held instruments"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            <TrendingUp size={13} className={dataOp === 'prices' ? 'animate-pulse' : ''} />
            {dataOp === 'prices' ? 'Fetching…' : 'Fetch prices'}
          </button>
          <button
            onClick={() => handleDataOp('macro')}
            disabled={!!dataOp || convening}
            title="Fetch FRED macro series (yield curve, VIX, CPI…)"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            <BarChart2 size={13} className={dataOp === 'macro' ? 'animate-pulse' : ''} />
            {dataOp === 'macro' ? 'Fetching…' : 'Fetch macro'}
          </button>
          <button
            onClick={() => handleDataOp('edgar')}
            disabled={!!dataOp || convening}
            title="Fetch annual fundamentals from SEC EDGAR (P/E, D/E, revenue…)"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            <FileText size={13} className={dataOp === 'edgar' ? 'animate-pulse' : ''} />
            {dataOp === 'edgar' ? 'Fetching…' : 'Fetch EDGAR'}
          </button>
          <button
            onClick={() => handleDataOp('sleeves')}
            disabled={!!dataOp || convening}
            title="Auto-assign equity_us / fixed_income / alternatives sleeves to unclassified instruments"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            <Tag size={13} className={dataOp === 'sleeves' ? 'animate-pulse' : ''} />
            {dataOp === 'sleeves' ? 'Classifying…' : 'Classify sleeves'}
          </button>
          <button
            onClick={() => handleDataOp('bundles')}
            disabled={!!dataOp || convening}
            title="Seed all bundle instruments (ETF Core, Dow 30, Nasdaq Top 50)"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-900 hover:bg-slate-800 text-slate-400 hover:text-slate-200 border border-slate-700 disabled:opacity-40"
          >
            <Boxes size={13} className={dataOp === 'bundles' ? 'animate-pulse' : ''} />
            {dataOp === 'bundles' ? 'Seeding…' : 'Seed bundles'}
          </button>
          <select
            value={constraint}
            onChange={e => setConstraint(e.target.value)}
            disabled={convening}
            className="bg-slate-900 border border-slate-700 text-slate-300 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none disabled:opacity-50 font-mono"
          >
            {profiles.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <select
            value={regime}
            onChange={async e => {
              const next = e.target.value
              setRegimeSaving(true)
              try {
                const res = await fetch('/api/config/regime', {
                  method: 'PUT',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ tilt: next }),
                })
                if (res.ok) setRegime(next)
              } finally {
                setRegimeSaving(false)
              }
            }}
            disabled={convening || regimeSaving}
            title="Override the macro regime tilt (takes effect on next Re-convene)"
            className="bg-slate-900 border border-slate-700 text-xs rounded-lg px-2.5 py-1.5 focus:outline-none disabled:opacity-50 font-mono"
            style={{
              color: regime === 'defensive' ? '#f87171' : regime === 'aggressive' ? '#4ade80' : '#94a3b8'
            }}
          >
            <option value="neutral">neutral</option>
            <option value="aggressive">aggressive</option>
            <option value="defensive">defensive</option>
          </select>
          <button
            onClick={handleConvene}
            disabled={convening || !!dataOp}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700 disabled:opacity-50"
          >
            <RefreshCw size={13} className={convening ? 'animate-spin' : ''} />
            {convening ? 'Convening…' : 'Re-convene'}
          </button>
        </div>
      </div>
      {error && (
        <div className="px-4 py-2 rounded-lg bg-rose-950/30 border border-rose-900/40 text-xs text-rose-400 font-mono">
          {error}
        </div>
      )}
      {dataMsg && (
        <div className="px-4 py-2 rounded-lg bg-slate-900 border border-slate-700 text-xs text-slate-400 font-mono flex items-center justify-between">
          <span>{dataMsg}</span>
          <button onClick={() => setDataMsg(null)} className="text-slate-600 hover:text-slate-400 ml-4">✕</button>
        </div>
      )}

      {/* Bundle toggles */}
      {bundles.length > 0 && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-slate-600 font-medium uppercase tracking-wider mr-1">
            Buy universe
          </span>
          {bundles.map(bundle => {
            const isToggling = bundleToggling === bundle.id
            return (
              <button
                key={bundle.id}
                onClick={() => handleBundleToggle(bundle.id, !bundle.enabled)}
                disabled={!!bundleToggling || !!dataOp || convening}
                title={bundle.display_name}
                className={`flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-all border disabled:opacity-50 ${
                  bundle.enabled
                    ? 'bg-indigo-950/60 border-indigo-600/50 text-indigo-300 hover:bg-indigo-950/80'
                    : 'bg-slate-900 border-slate-700 text-slate-500 hover:text-slate-300 hover:border-slate-600'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${
                    isToggling ? 'animate-pulse bg-indigo-400' :
                    bundle.enabled ? 'bg-indigo-400' : 'bg-slate-700'
                  }`}
                />
                {bundle.id.replace(/_/g, ' ')}
                <span className="font-mono opacity-60">{bundle.instrument_count}</span>
              </button>
            )
          })}
          <span className="text-xs text-slate-700 font-mono">
            {bundles.filter(b => b.enabled).reduce((s, b) => s + b.instrument_count, 0)} instruments active
          </span>
        </div>
      )}

      {/* Oracle Cards */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">
          Oracle Verdicts
        </h2>
        <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
          {data.oracle_cards.map(card => (
            <OracleCardView key={card.oracle_id} card={card} />
          ))}
        </div>
      </section>

      {/* Dissent Matrix */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">
          Dissent Matrix
        </h2>
        <DissentMatrix rows={data.dissent_matrix} oracleAbstained={abstainedOracles} />
      </section>

      {/* Rivals' Objections */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">
          Rivals' Objections
          <span className="ml-2 text-slate-600 normal-case">({data.rival_objections.length})</span>
        </h2>
        <ObjectionsList objections={data.rival_objections} />
      </section>
    </div>
  )
}
