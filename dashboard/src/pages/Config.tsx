import { useEffect, useState } from 'react'
import { api, ConfigResponse } from '../api'

export function Config() {
  const [data, setData] = useState<ConfigResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.getConfig().then(setData).catch(e => setError(String(e))).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>
  if (error) return <div className="p-4 text-slate-400 text-sm">{error}</div>
  if (!data) return null

  const params = [
    {
      key: 'drift_abs',
      label: 'Drift band (absolute)',
      value: `${(data.drift_abs * 100).toFixed(0)}%`,
      description: 'Sleeves outside this threshold from target trigger rebalancing.',
    },
    {
      key: 'drift_rel',
      label: 'Drift band (relative)',
      value: `${(data.drift_rel * 100).toFixed(0)}%`,
      description: 'Relative drift threshold (applied alongside absolute band).',
    },
    {
      key: 'min_trade_usd',
      label: 'Minimum trade size',
      value: `$${data.min_trade_usd.toFixed(0)}`,
      description: 'Proposals below this value are filtered out.',
    },
    {
      key: 'new_money',
      label: 'New money',
      value: `$${data.new_money.toFixed(0)}`,
      description: 'Fresh cash to route to under-target sleeves before selling.',
    },
  ]

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Config</h1>
        <p className="text-sm text-slate-500 mt-1">
          Engine parameters — read-only display. Adjust via CLI flags or the Trades panel recompute.
        </p>
      </div>

      {/* Engine parameters */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Engine Defaults</h2>
        <div className="rounded-xl border border-slate-800 bg-slate-900 divide-y divide-slate-800">
          {params.map(p => (
            <div key={p.key} className="flex items-center justify-between px-5 py-4">
              <div>
                <p className="text-sm font-medium text-slate-200">{p.label}</p>
                <p className="text-xs text-slate-600 mt-0.5">{p.description}</p>
              </div>
              <span className="font-mono text-lg font-semibold text-blue-400 tabular">{p.value}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Constraint profiles */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Constraint Profiles</h2>
        <div className="flex flex-wrap gap-3">
          {data.available_profiles.map(profile => (
            <div key={profile} className="px-4 py-3 rounded-xl border border-slate-800 bg-slate-900">
              <p className="text-sm font-medium text-slate-200 font-mono">{profile}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Disclaimer */}
      <section className="rounded-lg border border-slate-800 bg-slate-900/50 p-4">
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-600 mb-2">About these parameters</h2>
        <p className="text-xs text-slate-600 leading-relaxed">
          These are engine parameters only — they control drift detection and trade sizing.
          They are never market inputs (no prices, no expected returns, no forecasts are set here).
          Changes to drift bands or min trade size only affect proposal generation, not scoring.
          To apply different parameters, use <code className="text-slate-500">committee convene --help</code> or the Trades panel recompute.
        </p>
      </section>
    </div>
  )
}
