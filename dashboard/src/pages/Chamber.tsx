import { useEffect, useState } from 'react'
import { AlertTriangle, Columns3 } from 'lucide-react'
import { api, OracleCard, DissentRow, RivalObjection } from '../api'
import { ORACLE_IDS, ORACLE_COLOR, ORACLE_DISPLAY, fmtScore, fmtPct, scoreColor, DIRECTION_COLOR } from '../constants'

function useChain<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    loader().then(setData).catch(e => setError(String(e))).finally(() => setLoading(false))
  }, [])
  return { data, error, loading }
}

function OracleCardView({ card }: { card: OracleCard }) {
  const color = ORACLE_COLOR[card.oracle_id] ?? '#64748b'
  return (
    <div
      className="rounded-xl overflow-hidden flex flex-col"
      style={{
        background: `linear-gradient(160deg, ${color}12 0%, #0f172a 40%)`,
        border: `1px solid ${color}30`,
        boxShadow: `0 0 0 0 transparent, inset 0 1px 0 ${color}20`,
      }}
    >
      {/* Thick colored header band */}
      <div
        className="h-[3px] flex-shrink-0"
        style={{ background: color }}
      />

      {/* Oracle identity */}
      <div
        className="px-4 pt-3 pb-2.5 flex items-start justify-between"
        style={{ borderBottom: `1px solid ${color}18` }}
      >
        <div className="flex items-center gap-2.5 min-w-0">
          {/* Color swatch dot */}
          <span
            className="flex-shrink-0 w-2.5 h-2.5 rounded-full mt-0.5"
            style={{ background: color, boxShadow: `0 0 6px ${color}80` }}
          />
          <div className="min-w-0">
            <h3 className="text-base font-bold leading-tight tracking-tight" style={{ color }}>
              {card.display_name}
            </h3>
            <p className="text-[10px] text-slate-600 font-mono uppercase tracking-widest mt-0.5 truncate">
              {card.oracle_id}
            </p>
          </div>
        </div>
        {card.abstained ? (
          <span
            className="text-xs px-2 py-0.5 rounded-full font-mono flex-shrink-0 ml-2"
            style={{ background: `${color}18`, color: `${color}bb`, border: `1px solid ${color}30` }}
          >
            abstain
          </span>
        ) : (
          <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 flex-shrink-0 ml-2 font-mono">
            {card.scored_count} scored
          </span>
        )}
      </div>

      <div className="p-4 flex-1 space-y-3">
        {card.regime_state && (
          <div
            className="px-2.5 py-1.5 rounded-lg text-xs flex items-center gap-2"
            style={{ background: `${color}10`, border: `1px solid ${color}20` }}
          >
            <span className="text-slate-500">Regime</span>
            <span className="font-mono font-medium" style={{ color }}>{card.regime_state}</span>
          </div>
        )}

        {/* Top scores */}
        <div className="space-y-1.5">
          {card.top_scores.length === 0 ? (
            <p className="text-xs text-slate-600 italic">No scores available.</p>
          ) : (
            card.top_scores.map(hs => (
              <div key={hs.instrument_id} className="flex items-center justify-between gap-2">
                <span className="text-xs text-slate-400 font-mono w-14 flex-shrink-0">#{hs.instrument_id}</span>
                <div className="flex-1 h-1 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all"
                    style={{
                      width: `${Math.abs((hs.score ?? 0) * 50) + 50}%`,
                      background: scoreColor(hs.score),
                      marginLeft: (hs.score ?? 0) < 0 ? 'auto' : undefined,
                    }}
                  />
                </div>
                <span className="text-xs font-mono tabular w-10 text-right flex-shrink-0" style={{ color: scoreColor(hs.score) }}>
                  {fmtScore(hs.score)}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Sleeve targets */}
        <div className="pt-2.5 border-t border-slate-800/60">
          <p className="text-xs text-slate-600 mb-1.5 uppercase tracking-wider font-medium">Sleeve targets</p>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            {Object.entries(card.sleeve_targets).map(([sleeve, target]) => (
              <div key={sleeve} className="flex justify-between text-xs">
                <span className="text-slate-500 truncate">{sleeve.replace('_', ' ')}</span>
                <span className="font-mono text-slate-300 tabular">{fmtPct(parseFloat(target))}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="text-xs text-slate-600 font-mono">{card.proposal_count} proposals</div>
      </div>
    </div>
  )
}

function DissentMatrix({ rows }: { rows: DissentRow[] }) {
  const oracles = ORACLE_IDS

  if (rows.length === 0) {
    return <p className="text-slate-600 text-sm">No instruments with proposals.</p>
  }

  return (
    <div className="overflow-x-auto rounded-xl border border-slate-800 bg-slate-900">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            {/* Ticker column header — neutral bar to match oracle columns */}
            <th
              className="text-left px-4 py-0 text-slate-500 font-medium w-36"
              style={{ borderRight: '1px solid #1e293b' }}
            >
              <div className="h-1 w-full mb-2 bg-slate-800" />
              <div className="pb-2 pt-0 text-slate-500 text-xs">Ticker</div>
            </th>
            {oracles.map(oid => {
              const color = ORACLE_COLOR[oid]
              const shortName = ORACLE_DISPLAY[oid]?.split(' ')[0] ?? oid
              return (
                <th
                  key={oid}
                  className="px-2 py-0 text-center w-20"
                  style={{ borderRight: '1px solid #1e293b' }}
                >
                  {/* Colored top bar per column */}
                  <div
                    className="w-full h-1 mb-2"
                    style={{ background: color, opacity: 0.85 }}
                  />
                  <div className="pb-2 font-mono text-xs font-semibold" style={{ color }}>
                    {shortName}
                  </div>
                </th>
              )
            })}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, ri) => (
            <tr
              key={row.instrument_id}
              className={`hover:bg-slate-800/40 transition-colors ${
                ri < rows.length - 1 ? 'border-b border-slate-800/60' : ''
              }`}
            >
              <td
                className="px-4 py-2.5"
                style={{ borderRight: '1px solid #1e293b' }}
              >
                <span className="font-mono font-semibold text-slate-100">{row.ticker ?? `#${row.instrument_id}`}</span>
                {row.name && (
                  <span className="text-slate-500 ml-2 text-xs truncate">{row.name.slice(0, 18)}</span>
                )}
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
          ))}
        </tbody>
      </table>
    </div>
  )
}

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
          <li key={i} className="flex items-center gap-3 p-3 rounded-lg bg-slate-900 border border-slate-800 text-sm">
            <span className="font-mono font-semibold text-slate-200">{obj.ticker ?? `#${obj.instrument_id}`}</span>
            <span className="text-slate-700">·</span>
            <span style={{ color: colorA }} className="font-medium">{ORACLE_DISPLAY[obj.oracle_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono font-bold"
              style={{ color: DIRECTION_COLOR[obj.oracle_direction], background: `${DIRECTION_COLOR[obj.oracle_direction]}1a`, border: `1px solid ${DIRECTION_COLOR[obj.oracle_direction]}40` }}
            >
              {obj.oracle_direction.toUpperCase()}
            </span>
            <span className="text-slate-600 text-xs">vs</span>
            <span style={{ color: colorB }} className="font-medium">{ORACLE_DISPLAY[obj.rival_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono font-bold"
              style={{ color: DIRECTION_COLOR[obj.rival_direction], background: `${DIRECTION_COLOR[obj.rival_direction]}1a`, border: `1px solid ${DIRECTION_COLOR[obj.rival_direction]}40` }}
            >
              {obj.rival_direction.toUpperCase()}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

function EmptyState({ error }: { error: string | null }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
      <div className="flex flex-col items-center gap-4">
        {/* Logo-inspired oracle column marks */}
        <div className="flex items-end gap-1.5 h-10">
          {Object.entries(ORACLE_COLOR).map(([id, color]) => (
            <div
              key={id}
              className="w-2.5 rounded-t"
              style={{
                height: `${28 + Math.random() * 12}px`,
                background: color,
                opacity: 0.5,
              }}
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

  return (
    <div className="space-y-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold text-white">The Chamber</h1>
        <p className="text-sm text-slate-500 mt-1 font-mono">
          run {data.run_id.slice(0, 8)}… &middot; {new Date(data.run_at).toLocaleString()}
          {data.scenario_id && <span className="ml-2 text-violet-400">scenario: {data.scenario_id}</span>}
        </p>
      </div>

      {/* Oracle Cards Grid */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Oracle Verdicts</h2>
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
        <DissentMatrix rows={data.dissent_matrix} />
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
