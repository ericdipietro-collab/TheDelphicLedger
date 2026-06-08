import { useState, useEffect } from 'react'
import { Play } from 'lucide-react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, Cell, ResponsiveContainer, ReferenceLine } from 'recharts'
import { api, PackSummary, ScenarioResult } from '../api'
import { ORACLE_COLOR, SLEEVE_LABELS, fmtMoney, fmtPct } from '../constants'

function WaterfallChart({ data }: { data: ScenarioResult }) {
  const chartData = data.waterfall.map(w => ({
    sleeve: SLEEVE_LABELS[w.sleeve] ?? w.sleeve,
    before: parseFloat(w.before_mv),
    after: parseFloat(w.after_mv),
    delta: parseFloat(w.delta_mv),
    shock: w.shock_pct,
  }))

  return (
    <div className="space-y-4">
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={chartData} margin={{ left: 8, right: 8 }}>
          <XAxis dataKey="sleeve" tick={{ fontSize: 11, fill: '#94a3b8' }} axisLine={false} tickLine={false} />
          <YAxis
            tickFormatter={v => `$${(v / 1000).toFixed(0)}k`}
            tick={{ fontSize: 10, fill: '#64748b' }}
            axisLine={false}
            tickLine={false}
            width={60}
          />
          <Tooltip
            formatter={(v: number, name: string) => [fmtMoney(String(v)), name]}
            contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, fontSize: 12 }}
            itemStyle={{ color: '#e2e8f0' }}
          />
          <ReferenceLine y={0} stroke="#334155" />
          <Bar dataKey="before" name="Before" fill="#3b82f6" opacity={0.5} radius={[4, 4, 0, 0]} />
          <Bar dataKey="after" name="After" radius={[4, 4, 0, 0]}>
            {chartData.map((entry, i) => (
              <Cell key={i} fill={entry.delta < 0 ? '#f43f5e' : '#10b981'} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>

      {/* Shock table */}
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-slate-800">
            <th className="text-left px-3 py-2 text-slate-500 font-medium">Sleeve</th>
            <th className="text-right px-3 py-2 text-slate-500 font-medium">Before</th>
            <th className="text-right px-3 py-2 text-slate-500 font-medium">Shock</th>
            <th className="text-right px-3 py-2 text-slate-500 font-medium">After</th>
            <th className="text-right px-3 py-2 text-slate-500 font-medium">Δ</th>
          </tr>
        </thead>
        <tbody>
          {chartData.map(row => (
            <tr key={row.sleeve} className="border-b border-slate-800/50">
              <td className="px-3 py-2 text-slate-300">{row.sleeve}</td>
              <td className="px-3 py-2 text-right font-mono tabular text-slate-400">{fmtMoney(String(row.before))}</td>
              <td className="px-3 py-2 text-right font-mono tabular" style={{ color: row.shock < 0 ? '#f43f5e' : '#10b981' }}>
                {fmtPct(row.shock)}
              </td>
              <td className="px-3 py-2 text-right font-mono tabular text-slate-200">{fmtMoney(String(row.after))}</td>
              <td className="px-3 py-2 text-right font-mono tabular" style={{ color: row.delta < 0 ? '#f43f5e' : '#10b981' }}>
                {fmtMoney(String(row.delta))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function VerdictDeltas({ data }: { data: ScenarioResult }) {
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="border-b border-slate-800">
          <th className="text-left px-3 py-2 text-slate-500 font-medium">Oracle</th>
          <th className="text-center px-3 py-2 text-slate-500 font-medium">Baseline scored</th>
          <th className="text-center px-3 py-2 text-slate-500 font-medium">Scenario scored</th>
          <th className="text-center px-3 py-2 text-slate-500 font-medium">Baseline abstained</th>
          <th className="text-center px-3 py-2 text-slate-500 font-medium">Scenario abstained</th>
        </tr>
      </thead>
      <tbody>
        {data.verdict_deltas.map(d => {
          const color = ORACLE_COLOR[d.oracle_id] ?? '#64748b'
          return (
            <tr key={d.oracle_id} className="border-b border-slate-800/50">
              <td className="px-3 py-2">
                <span className="font-medium" style={{ color }}>{d.display_name}</span>
              </td>
              <td className="px-3 py-2 text-center font-mono tabular text-slate-300">{d.scored_count_baseline}</td>
              <td className="px-3 py-2 text-center font-mono tabular text-slate-200">{d.scored_count_scenario}</td>
              <td className="px-3 py-2 text-center">
                {d.abstained_baseline
                  ? <span className="text-slate-500 font-mono">abstain</span>
                  : <span className="text-emerald-500 font-mono">active</span>}
              </td>
              <td className="px-3 py-2 text-center">
                {d.abstained_scenario
                  ? <span className="text-rose-500 font-mono">abstain</span>
                  : <span className="text-emerald-500 font-mono">active</span>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function Scenarios() {
  const [packs, setPacks] = useState<PackSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [result, setResult] = useState<ScenarioResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [resultLoading, setResultLoading] = useState(false)
  const [running, setRunning] = useState(false)

  const reloadPacks = () =>
    api.listScenarios().then(pks => {
      setPacks(pks)
      const first = pks.find(p => p.has_run)
      if (first && !selected) setSelected(first.pack_id)
    }).catch(e => setError(String(e)))

  useEffect(() => {
    reloadPacks().finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!selected) return
    setResultLoading(true)
    api.getScenario(selected)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setResultLoading(false))
  }, [selected])

  const handleRun = async (pack_id: string) => {
    setRunning(true)
    setError(null)
    try {
      const res = await api.runScenario(pack_id)
      setResult(res)
      setSelected(pack_id)
      setPacks(prev => prev.map(p => p.pack_id === pack_id ? { ...p, has_run: true } : p))
    } catch (e) {
      setError(String(e))
    } finally {
      setRunning(false)
    }
  }

  if (loading) return <div className="text-slate-500 animate-pulse p-4">Loading…</div>

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-white">Scenario Theater</h1>
        <p className="text-sm text-slate-500 mt-1">Stress-test the portfolio with historical and hypothetical shocks.</p>
      </div>

      {error && (
        <div className="px-4 py-2 rounded-lg bg-rose-950/30 border border-rose-900/40 text-xs text-rose-400 font-mono">
          {error}
        </div>
      )}

      {/* Pack picker */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-3">
        {packs.map(pack => (
          <div
            key={pack.pack_id}
            className={`relative p-4 rounded-xl border transition-colors cursor-pointer ${
              selected === pack.pack_id
                ? 'border-blue-500 bg-blue-500/10'
                : 'border-slate-800 bg-slate-900 hover:border-slate-600'
            }`}
            onClick={() => pack.has_run && setSelected(pack.pack_id)}
          >
            <div className="flex items-start justify-between mb-1 gap-2">
              <span className="text-sm font-medium text-slate-200">{pack.display_name}</span>
              {pack.has_run
                ? <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded bg-emerald-500/10 text-emerald-400">run</span>
                : <span className="flex-shrink-0 text-xs px-1.5 py-0.5 rounded bg-slate-800 text-slate-500">no run</span>
              }
            </div>
            <p className="text-xs text-slate-600 font-mono mb-3">{pack.pack_type} &middot; {pack.pack_id}</p>
            <button
              onClick={e => { e.stopPropagation(); handleRun(pack.pack_id) }}
              disabled={running}
              className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-medium transition-colors bg-slate-800 hover:bg-slate-700 text-slate-300 border border-slate-700 disabled:opacity-40"
            >
              <Play size={11} />
              {running && selected === pack.pack_id ? 'Running…' : 'Run'}
            </button>
          </div>
        ))}
      </div>

      {/* Results */}
      {selected && (
        resultLoading ? (
          <div className="text-slate-500 animate-pulse p-4">Loading scenario…</div>
        ) : result ? (
          <div className="space-y-6">
            {/* Honesty note */}
            {result.honesty_note && (
              <div className="rounded-lg border border-amber-900/40 bg-amber-950/20 px-4 py-3 text-xs text-amber-400/80">
                <span className="font-semibold text-amber-400">Note: </span>{result.honesty_note.trim()}
              </div>
            )}

            {/* Waterfall */}
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">
                Before / After Valuation — {result.display_name}
              </h2>
              <WaterfallChart data={result} />
            </div>

            {/* Verdict deltas */}
            <div className="rounded-xl border border-slate-800 bg-slate-900 p-5">
              <h2 className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-4">Oracle Verdict Deltas</h2>
              <VerdictDeltas data={result} />
            </div>
          </div>
        ) : (
          <div className="p-5 rounded-xl border border-slate-800 bg-slate-900 flex items-center gap-4">
            <div>
              <p className="text-sm text-slate-400 font-medium mb-0.5">No run yet for <span className="font-mono text-slate-200">{selected}</span></p>
              <p className="text-xs text-slate-600">Click Run on the pack above to execute this scenario.</p>
            </div>
            <button
              onClick={() => handleRun(selected)}
              disabled={running}
              className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium bg-blue-600 hover:bg-blue-500 text-white disabled:opacity-50 transition-colors"
            >
              <Play size={13} />
              {running ? 'Running…' : 'Run now'}
            </button>
          </div>
        )
      )}
    </div>
  )
}
