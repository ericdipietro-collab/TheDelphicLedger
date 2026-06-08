import { useState, useEffect } from 'react'
import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, ReferenceLine } from 'recharts'
import { AlertTriangle, Wallet } from 'lucide-react'
import { api, PortfolioResponse } from '../api'
import { ORACLE_IDS, ORACLE_COLOR, ORACLE_DISPLAY, SLEEVE_COLORS, SLEEVE_LABELS, fmtMoney, fmtPct, fmtScore, scoreColor } from '../constants'

export function Portfolio() {
  const [oracle, setOracle] = useState('value_purist')
  const [data, setData] = useState<PortfolioResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    api.getPortfolio(oracle)
      .then(setData)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
  }, [oracle])

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>
  if (error) return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
      <Wallet size={32} className="text-slate-700" />
      <div className="text-center">
        <p className="text-slate-400 font-medium mb-1">No portfolio data</p>
        <p className="text-sm text-slate-600 max-w-sm">Import holdings to populate the portfolio view.</p>
      </div>
      <div className="px-4 py-2.5 rounded-lg bg-slate-900 border border-slate-800 font-mono text-xs text-slate-400">
        committee import
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

  const pieData = data.allocations.map((a, i) => ({
    name: SLEEVE_LABELS[a.sleeve] ?? a.sleeve,
    value: parseFloat(a.market_value),
    color: SLEEVE_COLORS[i % SLEEVE_COLORS.length],
  })).filter(d => d.value > 0)

  const driftData = data.drift_gauges.map(g => ({
    sleeve: SLEEVE_LABELS[g.sleeve] ?? g.sleeve,
    drift: g.drift_abs * 100,
    outside: g.outside_band,
  }))

  return (
    <div className="space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-white">Portfolio</h1>
          <p className="text-sm text-slate-500 mt-1 font-mono">as of {data.as_of} &middot; {fmtMoney(data.total_market_value)}</p>
        </div>
        {/* Oracle selector for drift targets */}
        <select
          value={oracle}
          onChange={e => setOracle(e.target.value)}
          className="bg-slate-800 border border-slate-700 text-slate-200 text-sm rounded-lg px-3 py-1.5 focus:outline-none focus:ring-1 focus:ring-slate-600"
        >
          {ORACLE_IDS.map(oid => (
            <option key={oid} value={oid}>{ORACLE_DISPLAY[oid]}</option>
          ))}
        </select>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-2 gap-6">
        {/* Allocation donut */}
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Allocation</h2>
          <div className="flex items-center gap-6">
            <ResponsiveContainer width="50%" height={180}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  dataKey="value"
                  strokeWidth={0}
                >
                  {pieData.map((entry, index) => (
                    <Cell key={index} fill={entry.color} />
                  ))}
                </Pie>
                <Tooltip
                  formatter={(v: number) => fmtMoney(String(v))}
                  contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                  itemStyle={{ color: '#e2e8f0' }}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="space-y-2 flex-1">
              {pieData.map((d) => (
                <div key={d.name} className="flex items-center gap-2">
                  <div className="w-2.5 h-2.5 rounded-full flex-shrink-0" style={{ background: d.color }} />
                  <span className="text-xs text-slate-400 flex-1">{d.name}</span>
                  <span className="text-xs font-mono text-slate-200 tabular">
                    {fmtPct(d.value / parseFloat(data.total_market_value))}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Drift gauges */}
        <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
          <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">
            Drift vs <span style={{ color: ORACLE_COLOR[oracle] }}>{ORACLE_DISPLAY[oracle]}</span> targets
          </h2>
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={driftData} layout="vertical" margin={{ left: 8, right: 16 }}>
              <XAxis
                type="number"
                tickFormatter={v => `${v.toFixed(0)}%`}
                tick={{ fontSize: 10, fill: '#64748b' }}
                axisLine={false}
                tickLine={false}
                domain={[-20, 20]}
              />
              <YAxis
                type="category"
                dataKey="sleeve"
                width={80}
                tick={{ fontSize: 10, fill: '#94a3b8' }}
                axisLine={false}
                tickLine={false}
              />
              <ReferenceLine x={0} stroke="#334155" />
              <Bar dataKey="drift" radius={[0, 3, 3, 0]}>
                {driftData.map((entry, i) => (
                  <Cell key={i} fill={entry.outside ? '#f43f5e' : '#3b82f6'} />
                ))}
              </Bar>
              <Tooltip
                formatter={(v: number) => [`${v.toFixed(1)}%`, 'Drift']}
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
                itemStyle={{ color: '#e2e8f0' }}
              />
            </BarChart>
          </ResponsiveContainer>
          <p className="text-xs text-slate-600 mt-2">Red = outside 5% abs band</p>
        </div>
      </div>

      {/* Holdings table */}
      <section>
        <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-3">Holdings</h2>
        <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-slate-800">
                  <th className="text-left px-4 py-3 text-slate-500 font-medium">Ticker</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">Type</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">Sleeve</th>
                  <th className="text-left px-3 py-3 text-slate-500 font-medium">Account</th>
                  <th className="text-right px-3 py-3 text-slate-500 font-medium">Qty</th>
                  <th className="text-right px-4 py-3 text-slate-500 font-medium">Market Value</th>
                  {ORACLE_IDS.map(oid => (
                    <th key={oid} className="text-center px-2 py-3 text-slate-600 font-medium w-16" style={{ color: ORACLE_COLOR[oid] + '99' }}>
                      {ORACLE_DISPLAY[oid]?.split(' ')[0]?.slice(0, 6)}
                    </th>
                  ))}
                  <th className="text-center px-3 py-3 text-slate-500 font-medium">8-K</th>
                </tr>
              </thead>
              <tbody>
                {data.holdings.length === 0 ? (
                  <tr>
                    <td colSpan={9 + ORACLE_IDS.length} className="px-4 py-8 text-center text-slate-600">
                      No holdings. Run <code>committee import</code> first.
                    </td>
                  </tr>
                ) : (
                  data.holdings.map((h, i) => (
                    <tr key={`${h.instrument_id}-${h.account_id}-${i}`} className="border-b border-slate-800/50 hover:bg-slate-800/30">
                      <td className="px-4 py-2.5">
                        <span className="font-mono font-medium text-slate-200">{h.ticker ?? `#${h.instrument_id}`}</span>
                        {h.name && <span className="text-slate-500 ml-1.5">{h.name.slice(0, 20)}</span>}
                      </td>
                      <td className="px-3 py-2.5 text-slate-500">{h.instrument_type ?? '—'}</td>
                      <td className="px-3 py-2.5 text-slate-500">{SLEEVE_LABELS[h.sleeve ?? ''] ?? h.sleeve ?? '—'}</td>
                      <td className="px-3 py-2.5 text-slate-500 font-mono">{h.account_id ?? '—'}</td>
                      <td className="px-3 py-2.5 text-right font-mono tabular text-slate-300">{h.qty}</td>
                      <td className="px-4 py-2.5 text-right font-mono tabular text-slate-200">{fmtMoney(h.market_value)}</td>
                      {ORACLE_IDS.map(oid => {
                        const score = h.oracle_scores[oid]
                        return (
                          <td key={oid} className="px-2 py-2.5 text-center font-mono tabular" style={{ color: scoreColor(score) }}>
                            {fmtScore(score)}
                          </td>
                        )
                      })}
                      <td className="px-3 py-2.5 text-center text-slate-600">—</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  )
}
