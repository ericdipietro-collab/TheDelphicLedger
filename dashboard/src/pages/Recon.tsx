import { useEffect, useState } from 'react'
import { api, ReconResponse } from '../api'

export function Recon() {
  const [data, setData] = useState<ReconResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState<'all' | 'open' | 'gap'>('open')

  useEffect(() => {
    api.getRecon().then(setData).catch(e => setError(String(e))).finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>
  if (error) return <div className="p-4 text-slate-400 text-sm">{error}</div>
  if (!data) return null

  const filtered = data.breaks.filter(b => {
    if (filter === 'open') return b.status === 'open'
    if (filter === 'gap') return b.coverage_gap
    return true
  })

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Recon</h1>
        <p className="text-sm text-slate-500 mt-1">Reconciliation breaks between projected and actual quantities.</p>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Open breaks', value: data.open_count, color: data.open_count > 0 ? '#f43f5e' : '#10b981' },
          { label: 'Coverage gaps', value: data.coverage_gap_count, color: data.coverage_gap_count > 0 ? '#f59e0b' : '#10b981' },
          { label: 'Resolved', value: data.resolved_count, color: '#94a3b8' },
        ].map(stat => (
          <div key={stat.label} className="rounded-xl border border-slate-800 bg-slate-900 p-5">
            <p className="text-xs text-slate-500 mb-1">{stat.label}</p>
            <p className="text-3xl font-semibold font-mono tabular" style={{ color: stat.color }}>
              {stat.value}
            </p>
          </div>
        ))}
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2">
        {(['all', 'open', 'gap'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors ${
              filter === f
                ? 'bg-slate-700 text-white'
                : 'text-slate-500 hover:text-slate-300 hover:bg-slate-800'
            }`}
          >
            {f === 'gap' ? 'Coverage gaps' : f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Breaks table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
        {filtered.length === 0 ? (
          <div className="p-8 text-center text-slate-600 text-sm">
            {filter === 'open' ? 'No open breaks. Portfolio reconciled.' : 'No breaks match this filter.'}
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800">
                  <th className="text-left px-4 py-3 text-slate-500 font-medium">Ticker</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">Account</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">As-of</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Expected</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Actual</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Δ</th>
                  <th className="text-center px-3 py-3 text-slate-500 font-medium">Status</th>
                  <th className="text-center px-3 py-3 text-slate-500 font-medium">Gap</th>
                  <th className="text-left px-4 py-3 text-slate-500 font-medium">Cause</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(b => (
                  <tr key={b.id} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                    <td className="px-4 py-2.5 font-mono font-medium text-slate-200">{b.ticker ?? `#${b.id}`}</td>
                    <td className="px-3 py-2.5 text-slate-500 font-mono">{b.account_id}</td>
                    <td className="px-3 py-2.5 text-slate-500 font-mono">{b.as_of}</td>
                    <td className="px-3 py-2.5 text-right font-mono tabular text-slate-400">{b.expected_qty ?? '—'}</td>
                    <td className="px-3 py-2.5 text-right font-mono tabular text-slate-400">{b.actual_qty ?? '—'}</td>
                    <td className="px-3 py-2.5 text-right font-mono tabular"
                      style={{ color: b.delta && parseFloat(b.delta) !== 0 ? '#f43f5e' : '#64748b' }}>
                      {b.delta ?? '—'}
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      <span className={`px-1.5 py-0.5 rounded text-xs font-mono ${
                        b.status === 'open' ? 'bg-rose-500/10 text-rose-400' : 'bg-slate-800 text-slate-500'
                      }`}>
                        {b.status}
                      </span>
                    </td>
                    <td className="px-3 py-2.5 text-center">
                      {b.coverage_gap
                        ? <span className="text-amber-400">⚠</span>
                        : <span className="text-slate-700">—</span>}
                    </td>
                    <td className="px-4 py-2.5 text-slate-500">{b.suggested_cause ?? b.resolution_note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
