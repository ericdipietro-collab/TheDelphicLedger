import { useEffect, useState } from 'react'
import {
  ShieldCheck, Rocket, Banknote, Globe, Cpu, LayoutGrid,
  AlertTriangle, Columns3,
} from 'lucide-react'
import { api, OracleCard, DissentRow, RivalObjection } from '../api'
import { ORACLE_IDS, ORACLE_COLOR, ORACLE_DISPLAY, fmtScore, fmtPct, scoreColor, DIRECTION_COLOR } from '../constants'

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
  value_purist:       { tagline: 'Margin of safety',    metrics: 'P/E · P/B · FCF · D/E' },
  growth_visionary:   { tagline: 'Category winners',    metrics: 'Rev YoY · Margin · Mom.' },
  yield_harvester:    { tagline: 'The check clears',    metrics: 'Distribution · Payout' },
  macro_tactician:    { tagline: 'Regime-aware ballast', metrics: 'T10Y3M · CPI · DXY · VIX' },
  quant:              { tagline: 'Humans are biased',   metrics: 'RSI · MA · 12-1 · β' },
  passive_pragmatist: { tagline: 'Cost & concentration', metrics: 'Expense · HHI · Turnover' },
} as Record<string, { tagline: string; metrics: string }>

// Strip leading "The " so "The Quant" → "Quant" in compact contexts
const ORACLE_SHORT = Object.fromEntries(
  Object.entries(ORACLE_DISPLAY).map(([id, name]) => [id, name.replace(/^The\s+/, '').split(' ')[0]])
) as Record<string, string>

// ── Hook ──────────────────────────────────────────────────────────────────────

function useChain<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    loader().then(setData).catch(e => setError(String(e))).finally(() => setLoading(false))
  }, [])
  return { data, error, loading }
}

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
                      #{hs.instrument_id}
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

  if (rows.length === 0) {
    return <p className="text-slate-600 text-sm">No instruments with proposals.</p>
  }

  return (
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
          {rows.map((row, ri) => {
            const split = isSplitRow(row.cells)
            return (
              <tr
                key={row.instrument_id}
                className={`hover:bg-slate-800/30 transition-colors ${
                  ri < rows.length - 1 ? 'border-b border-slate-800/60' : ''
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
                  return (
                    <td
                      key={cell.oracle_id}
                      className="px-2 py-2.5 text-center"
                      style={{ borderRight: '1px solid #1e293b' }}
                    >
                      {isHold ? (
                        <span className="text-slate-700 font-mono">—</span>
                      ) : (
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

// ── Empty / loading states ────────────────────────────────────────────────────

function EmptyState({ error }: { error: string | null }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <div className="flex flex-col items-center gap-4">
        <div className="flex items-end gap-1.5 h-10">
          {Object.entries(ORACLE_COLOR).map(([id, color]) => (
            <div
              key={id}
              className="w-2.5 rounded-t"
              style={{ height: `${28 + Math.random() * 12}px`, background: color, opacity: 0.4 }}
            />
          ))}
        </div>
        <div className="flex items-center gap-2 text-slate-400">
          <Columns3 size={18} className="text-slate-500" />
          <span className="text-base font-medium">The Chamber is empty</span>
        </div>
        <p className="text-sm text-slate-600 text-center max-w-sm">
          No deliberation run found. Convene the oracles to populate the Dissent Matrix.
        </p>
        <div className="mt-1 px-4 py-2.5 rounded-lg bg-slate-900 border border-slate-800 font-mono text-xs text-slate-400">
          committee convene
        </div>
      </div>
      {error && (
        <details className="max-w-sm w-full">
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
  )
}

// ── Chamber page ──────────────────────────────────────────────────────────────

export function Chamber() {
  const { data, error, loading } = useChain(() => api.getLatestChamber())

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
  if (error) return <EmptyState error={error} />
  if (!data) return null

  const abstainedOracles = new Set(
    data.oracle_cards.filter(c => c.abstained).map(c => c.oracle_id)
  )
  const abstainCount = abstainedOracles.size

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-white">The Chamber</h1>
          <p className="text-sm text-slate-500 mt-1 font-mono">
            run {data.run_id.slice(0, 8)}… &middot; {new Date(data.run_at).toLocaleString()}
            {data.scenario_id && <span className="ml-2 text-violet-400">scenario: {data.scenario_id}</span>}
          </p>
        </div>
        {/* Stat pill */}
        <div className="flex-shrink-0 flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900 border border-slate-800 text-xs font-mono">
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
      </div>

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
