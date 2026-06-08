import { useState, useEffect } from 'react'
import { AlertTriangle, ArrowLeftRight } from 'lucide-react'
import { api, TradesResponse, OracleProposals, ConfigResponse } from '../api'
import { ORACLE_IDS, ORACLE_COLOR, DIRECTION_COLOR, fmtMoney, fmtScore } from '../constants'

interface Params {
  constraint: string
  drift_abs: number
  min_trade_usd: number
  new_money: number
}

function ProposalTable({ oracleProposals }: { oracleProposals: OracleProposals[] }) {
  const [activeOracle, setActiveOracle] = useState<string>(ORACLE_IDS[0])

  const current = oracleProposals.find(op => op.oracle_id === activeOracle)

  return (
    <div>
      {/* Oracle tabs */}
      <div className="flex gap-1 mb-4 overflow-x-auto">
        {oracleProposals.map(op => {
          const color = ORACLE_COLOR[op.oracle_id] ?? '#64748b'
          const isActive = op.oracle_id === activeOracle
          return (
            <button
              key={op.oracle_id}
              onClick={() => setActiveOracle(op.oracle_id)}
              className={`flex-shrink-0 px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                isActive ? 'text-white' : 'text-slate-500 hover:text-slate-300'
              }`}
              style={isActive ? { background: color + '33', color } : {}}
            >
              {op.display_name}
              <span className="ml-1.5 opacity-60">({op.proposals.length})</span>
            </button>
          )
        })}
      </div>

      {/* Proposals table */}
      {!current || current.proposals.length === 0 ? (
        <p className="text-slate-600 text-sm py-6 text-center">No proposals for this oracle.</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-slate-800">
                <th className="text-left px-4 py-3 text-slate-500 font-medium">Instrument</th>
                <th className="text-center px-3 py-3 text-slate-500 font-medium">Direction</th>
                <th className="text-right px-3 py-3 text-slate-500 font-medium">Qty</th>
                <th className="text-right px-4 py-3 text-slate-500 font-medium">Est. Value</th>
                <th className="text-right px-3 py-3 text-slate-500 font-medium">Score</th>
                <th className="text-left px-3 py-3 text-slate-500 font-medium">Tags</th>
                <th className="text-left px-4 py-3 text-slate-500 font-medium">Tax Note</th>
              </tr>
            </thead>
            <tbody>
              {current.proposals.map((p, i) => {
                const dirColor = DIRECTION_COLOR[p.direction] ?? '#64748b'
                return (
                  <tr key={i} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-4 py-2.5">
                      <span className="font-mono font-medium text-slate-200">{p.ticker ?? `#${p.instrument_id}`}</span>
                      {p.name && <span className="text-slate-500 ml-1.5">{p.name.slice(0, 18)}</span>}
                      <div className="text-slate-600 mt-0.5">{p.account_id}</div>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span
                        className="px-2 py-0.5 rounded font-mono font-semibold"
                        style={{ color: dirColor, background: dirColor + '22' }}
                      >
                        {p.direction.toUpperCase()}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-right font-mono tabular text-slate-300">{p.qty}</td>
                    <td className="px-4 py-2.5 text-right font-mono tabular text-slate-200">{fmtMoney(p.estimated_value)}</td>
                    <td className="px-3 py-2.5 text-right font-mono tabular" style={{ color: p.oracle_score != null ? (p.oracle_score > 0 ? '#10b981' : '#f43f5e') : '#64748b' }}>
                      {fmtScore(p.oracle_score)}
                    </td>
                    <td className="px-3 py-2.5">
                      <div className="flex flex-wrap gap-1">
                        {p.rationale_tags.map(t => (
                          <span key={t} className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-400 text-xs font-mono">{t}</span>
                        ))}
                      </div>
                    </td>
                    <td className="px-4 py-2.5 text-slate-500 max-w-xs">
                      {p.tax_note ?? '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

export function Trades() {
  const [data, setData] = useState<TradesResponse | null>(null)
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [recomputing, setRecomputing] = useState(false)
  const [params, setParams] = useState<Params>({
    constraint: 'unconstrained',
    drift_abs: 0.05,
    min_trade_usd: 200,
    new_money: 0,
  })

  useEffect(() => {
    Promise.all([api.getTrades(), api.getConfig()])
      .then(([trades, cfg]) => {
        setData(trades)
        setConfig(cfg)
        setParams(p => ({
          ...p,
          drift_abs: cfg.drift_abs,
          min_trade_usd: cfg.min_trade_usd,
          new_money: cfg.new_money,
          constraint: cfg.available_profiles[0] ?? 'unconstrained',
        }))
      })
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [])

  const handleRecompute = async () => {
    if (!data) return
    setRecomputing(true)
    try {
      const result = await api.recomputeTrades({
        run_id: data.run_id,
        ...params,
        drift_rel: 0.25,
      })
      setData(result)
    } catch (e) {
      setError(String(e))
    } finally {
      setRecomputing(false)
    }
  }

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>
  if (error) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <ArrowLeftRight size={32} className="text-slate-700" />
      <div className="text-center">
        <p className="text-slate-400 font-medium mb-1">No trade proposals</p>
        <p className="text-sm text-slate-600 max-w-sm">Convene the oracles first to generate rebalancing proposals.</p>
      </div>
      <div className="px-4 py-2.5 rounded-lg bg-slate-900 border border-slate-800 font-mono text-xs text-slate-400">
        committee convene
      </div>
      <details className="max-w-sm w-full">
        <summary className="text-xs text-slate-700 cursor-pointer hover:text-slate-500 flex items-center gap-1.5 justify-center">
          <AlertTriangle size={12} />
          Technical detail
        </summary>
        <p className="mt-2 text-xs text-slate-700 font-mono break-all bg-slate-900 rounded p-2 border border-slate-800">{error}</p>
      </details>
    </div>
  )
  if (!data) return null

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Trades</h1>
        <p className="text-sm text-slate-500 mt-1 font-mono">run {data.run_id.slice(0, 8)}… &middot; {data.constraint ?? 'unconstrained'}</p>
      </div>

      {/* Config knob row */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Engine Parameters</h2>
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-5 items-end">
          <div>
            <label className="text-xs text-slate-500 mb-1.5 block">Constraint profile</label>
            <select
              value={params.constraint}
              onChange={e => setParams(p => ({ ...p, constraint: e.target.value }))}
              className="w-full bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg px-3 py-2 focus:outline-none"
            >
              {(config?.available_profiles ?? []).map(pr => (
                <option key={pr} value={pr}>{pr}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1.5 block">Drift band: {(params.drift_abs * 100).toFixed(0)}%</label>
            <input
              type="range" min={1} max={20} step={1}
              value={params.drift_abs * 100}
              onChange={e => setParams(p => ({ ...p, drift_abs: parseFloat(e.target.value) / 100 }))}
              className="w-full accent-blue-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1.5 block">Min trade: ${params.min_trade_usd.toFixed(0)}</label>
            <input
              type="range" min={0} max={2000} step={50}
              value={params.min_trade_usd}
              onChange={e => setParams(p => ({ ...p, min_trade_usd: parseFloat(e.target.value) }))}
              className="w-full accent-blue-500"
            />
          </div>
          <div>
            <label className="text-xs text-slate-500 mb-1.5 block">New money: ${params.new_money.toFixed(0)}</label>
            <input
              type="range" min={0} max={50000} step={500}
              value={params.new_money}
              onChange={e => setParams(p => ({ ...p, new_money: parseFloat(e.target.value) }))}
              className="w-full accent-blue-500"
            />
          </div>
        </div>
        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={handleRecompute}
            disabled={recomputing}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg transition-colors font-medium"
          >
            {recomputing ? 'Recomputing…' : 'Recompute (in-memory)'}
          </button>
          <p className="text-xs text-slate-600">Recompute runs the rebalancer in-memory — no DB writes.</p>
        </div>
      </div>

      {/* Proposals */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
        <ProposalTable oracleProposals={data.oracle_proposals} />
      </div>
    </div>
  )
}
