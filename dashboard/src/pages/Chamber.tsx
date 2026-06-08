import { useEffect, useState } from 'react'
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
      className="rounded-xl border bg-slate-900 overflow-hidden"
      style={{ borderColor: `${color}40` }}
    >
      {/* Colored header band */}
      <div className="h-1.5" style={{ background: color }} />
      <div className="p-4">
        <div className="flex items-start justify-between mb-3">
          <div>
            <h3 className="text-sm font-semibold mt-0.5" style={{ color }}>{card.display_name}</h3>
            <p className="text-xs text-slate-600 font-mono uppercase tracking-wider mt-0.5">{card.oracle_id}</p>
          </div>
          {card.abstained ? (
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-500">abstain</span>
          ) : (
            <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-300">{card.scored_count} scored</span>
          )}
        </div>

        {card.regime_state && (
          <div className="mb-3 px-2 py-1 rounded bg-slate-800 text-xs text-slate-400">
            Regime: <span className="text-slate-200 font-mono">{card.regime_state}</span>
          </div>
        )}

        {/* Top 3 scores */}
        <div className="space-y-1.5">
          {card.top_scores.length === 0 ? (
            <p className="text-xs text-slate-600">No scores available.</p>
          ) : (
            card.top_scores.map(hs => (
              <div key={hs.instrument_id} className="flex items-center justify-between">
                <span className="text-xs text-slate-400 font-mono">#{hs.instrument_id}</span>
                <div className="flex-1 mx-2 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.abs((hs.score ?? 0) * 50) + 50}%`,
                      background: scoreColor(hs.score),
                      marginLeft: (hs.score ?? 0) < 0 ? 'auto' : undefined,
                    }}
                  />
                </div>
                <span className="text-xs font-mono tabular" style={{ color: scoreColor(hs.score) }}>
                  {fmtScore(hs.score)}
                </span>
              </div>
            ))
          )}
        </div>

        {/* Sleeve targets */}
        <div className="mt-3 pt-3 border-t border-slate-800">
          <p className="text-xs text-slate-600 mb-1.5">Sleeve targets</p>
          <div className="grid grid-cols-2 gap-x-3 gap-y-0.5">
            {Object.entries(card.sleeve_targets).map(([sleeve, target]) => (
              <div key={sleeve} className="flex justify-between text-xs">
                <span className="text-slate-500 truncate">{sleeve.replace('_', ' ')}</span>
                <span className="font-mono text-slate-300 tabular">{fmtPct(parseFloat(target))}</span>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-2 text-xs text-slate-600">{card.proposal_count} proposals</div>
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
    <div className="overflow-x-auto">
      <table className="w-full text-xs border-collapse">
        <thead>
          <tr>
            <th className="text-left px-3 py-2 text-slate-500 font-medium border-b border-slate-800 w-24">Ticker</th>
            {oracles.map(oid => (
              <th key={oid} className="px-2 py-2 text-center border-b border-slate-800 w-20">
                <span className="text-xs font-mono" style={{ color: ORACLE_COLOR[oid] }}>
                  {ORACLE_DISPLAY[oid]?.split(' ')[0]}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.instrument_id} className="border-b border-slate-800/50 hover:bg-slate-900/50">
              <td className="px-3 py-2">
                <span className="font-mono text-slate-200">{row.ticker ?? `#${row.instrument_id}`}</span>
                {row.name && <span className="text-slate-500 ml-1.5 truncate text-xs">{row.name.slice(0, 18)}</span>}
              </td>
              {row.cells.map(cell => {
                const dir = cell.direction ?? 'hold'
                const color = DIRECTION_COLOR[dir] ?? '#64748b'
                return (
                  <td key={cell.oracle_id} className="px-2 py-2 text-center">
                    <span
                      className="inline-block px-1.5 py-0.5 rounded text-xs font-mono font-medium"
                      style={{ color, background: `${color}22` }}
                    >
                      {dir === 'hold' ? '—' : dir.toUpperCase()}
                    </span>
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
            <span className="text-slate-500">—</span>
            <span style={{ color: colorA }} className="font-medium">{ORACLE_DISPLAY[obj.oracle_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono"
              style={{ color: DIRECTION_COLOR[obj.oracle_direction], background: `${DIRECTION_COLOR[obj.oracle_direction]}22` }}
            >
              {obj.oracle_direction.toUpperCase()}
            </span>
            <span className="text-slate-600">vs</span>
            <span style={{ color: colorB }} className="font-medium">{ORACLE_DISPLAY[obj.rival_id]}</span>
            <span
              className="px-1.5 py-0.5 rounded text-xs font-mono"
              style={{ color: DIRECTION_COLOR[obj.rival_direction], background: `${DIRECTION_COLOR[obj.rival_direction]}22` }}
            >
              {obj.rival_direction.toUpperCase()}
            </span>
          </li>
        )
      })}
    </ul>
  )
}

export function Chamber() {
  const { data, error, loading } = useChain(() => api.getLatestChamber())

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>
  if (error) return (
    <div className="p-6 rounded-xl bg-slate-900 border border-slate-800">
      <p className="text-slate-400 text-sm mb-1">No data yet.</p>
      <p className="text-slate-600 text-xs font-mono">{error}</p>
      <p className="text-slate-600 text-xs mt-2">Run <code className="text-slate-400">committee convene</code> to generate a run.</p>
    </div>
  )
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
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-4">
          <DissentMatrix rows={data.dissent_matrix} />
        </div>
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
