import { useEffect, useState } from 'react'
import { CheckCircle, SkipForward, Search, Plus, Banknote, RefreshCw } from 'lucide-react'

interface UnresolvedItem {
  id: number
  raw_value: string
  context: Record<string, unknown> | null
}

interface InstrumentSummary {
  id: number
  ticker: string | null
  name: string | null
  instrument_type: string | null
  asset_class: string | null
  has_holdings: boolean
}

type ActionMode = 'idle' | 'map' | 'create'

const INSTRUMENT_TYPES = ['stock', 'etf', 'mutual_fund', 'cash', 'unclassified']

export function Resolve() {
  const [items, setItems] = useState<UnresolvedItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const [activeId, setActiveId] = useState<number | null>(null)
  const [actionMode, setActionMode] = useState<ActionMode>('idle')

  // map mode
  const [searchQ, setSearchQ] = useState('')
  const [searchResults, setSearchResults] = useState<InstrumentSummary[]>([])
  const [searching, setSearching] = useState(false)

  // create mode
  const [createTicker, setCreateTicker] = useState('')
  const [createName, setCreateName] = useState('')
  const [createType, setCreateType] = useState('etf')

  const [resolving, setResolving] = useState(false)
  const [doneMsg, setDoneMsg] = useState<string | null>(null)

  const activeItem = items.find(i => i.id === activeId) ?? null

  async function loadUnresolved() {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch('/api/instruments/unresolved')
      if (!r.ok) throw new Error(await r.text())
      const data: UnresolvedItem[] = await r.json()
      setItems(data)
      if (data.length > 0 && activeId === null) setActiveId(data[0].id)
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { loadUnresolved() }, [])

  async function doSearch(q: string) {
    setSearchQ(q)
    if (q.length < 1) { setSearchResults([]); return }
    setSearching(true)
    try {
      const r = await fetch(`/api/instruments/search?q=${encodeURIComponent(q)}`)
      if (r.ok) setSearchResults(await r.json())
    } finally {
      setSearching(false)
    }
  }

  function resetPanel() {
    setActionMode('idle')
    setSearchQ('')
    setSearchResults([])
    setCreateTicker('')
    setCreateName('')
    setCreateType('etf')
    setDoneMsg(null)
  }

  async function resolve(payload: object) {
    if (!activeItem) return
    setResolving(true)
    setDoneMsg(null)
    try {
      const r = await fetch('/api/instruments/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_value: activeItem.raw_value, ...payload }),
      })
      const result = await r.json()
      if (!r.ok) throw new Error(result.detail ?? JSON.stringify(result))
      setDoneMsg(result.message)
      // Remove resolved item and advance to next
      const remaining = items.filter(i => i.id !== activeItem.id)
      setItems(remaining)
      setActiveId(remaining.length > 0 ? remaining[0].id : null)
      resetPanel()
    } catch (e: unknown) {
      setError(String(e))
    } finally {
      setResolving(false)
    }
  }

  function skip() {
    if (!activeItem) return
    const idx = items.findIndex(i => i.id === activeItem.id)
    const next = items[(idx + 1) % items.length]
    setActiveId(next?.id ?? null)
    resetPanel()
  }

  if (loading) return (
    <div className="p-8 text-slate-400 text-sm">Loading unresolved instruments…</div>
  )

  return (
    <div className="p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Resolve Instruments</h1>
          <p className="text-sm text-slate-400 mt-0.5">
            {items.length === 0
              ? 'All instruments resolved.'
              : `${items.length} pending — map each raw symbol to a canonical instrument.`}
          </p>
        </div>
        <button
          onClick={loadUnresolved}
          className="p-2 rounded-lg text-slate-400 hover:text-slate-200 hover:bg-slate-800"
        >
          <RefreshCw size={16} />
        </button>
      </div>

      {error && (
        <div className="px-4 py-2 rounded-lg bg-rose-950/30 border border-rose-900/40 text-xs text-rose-400 font-mono">
          {error}
        </div>
      )}
      {doneMsg && (
        <div className="px-4 py-2 rounded-lg bg-emerald-950/30 border border-emerald-900/40 text-xs text-emerald-400 font-mono flex items-center gap-2">
          <CheckCircle size={14} /> {doneMsg}
        </div>
      )}

      {items.length === 0 && !loading && (
        <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-center text-slate-400 text-sm">
          No unresolved instruments. Holdings are fully mapped.
        </div>
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-[200px_1fr] gap-4">
          {/* Queue list */}
          <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden">
            <div className="px-3 py-2 text-xs font-medium text-slate-500 uppercase tracking-wider border-b border-slate-800 flex items-center justify-between">
              <span>Queue</span>
              {items.length > 1 && (
                <button
                  onClick={() => {
                    setItems([])
                    setActiveId(null)
                    resetPanel()
                    setDoneMsg(`Skipped ${items.length} items — they'll reappear on next load.`)
                  }}
                  className="text-slate-600 hover:text-slate-400 text-xs font-normal normal-case tracking-normal"
                >
                  Skip all
                </button>
              )}
            </div>
            {items.map(item => (
              <button
                key={item.id}
                onClick={() => { setActiveId(item.id); resetPanel() }}
                className={`w-full text-left px-3 py-2.5 text-sm font-mono border-b border-slate-800/50 last:border-0 transition-colors ${
                  activeId === item.id
                    ? 'bg-indigo-950/50 text-indigo-300'
                    : 'text-slate-300 hover:bg-slate-800/50'
                }`}
              >
                {item.raw_value}
              </button>
            ))}
          </div>

          {/* Action panel */}
          {activeItem && (
            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-5 space-y-5">
              <div>
                <div className="text-xs text-slate-500 uppercase tracking-wider">Raw symbol</div>
                <div className="text-2xl font-mono font-bold text-slate-100 mt-1">{activeItem.raw_value}</div>
              </div>

              {/* Action buttons */}
              {actionMode === 'idle' && (
                <div className="flex flex-wrap gap-2">
                  <button
                    onClick={() => setActionMode('map')}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-indigo-900/40 border border-indigo-800/60 text-indigo-300 text-sm hover:bg-indigo-900/60"
                  >
                    <Search size={14} /> Map to existing ticker
                  </button>
                  <button
                    onClick={() => { setActionMode('create'); setCreateTicker(activeItem.raw_value.replace('/', '-')) }}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-sm hover:bg-slate-700"
                  >
                    <Plus size={14} /> Create new instrument
                  </button>
                  <button
                    onClick={() => resolve({ action: 'cash' })}
                    disabled={resolving}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-slate-800 border border-slate-700 text-slate-300 text-sm hover:bg-slate-700"
                  >
                    <Banknote size={14} /> Mark as cash
                  </button>
                  <button
                    onClick={skip}
                    className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-slate-500 text-sm hover:text-slate-300"
                  >
                    <SkipForward size={14} /> Skip for now
                  </button>
                </div>
              )}

              {/* Map to existing */}
              {actionMode === 'map' && (
                <div className="space-y-3">
                  <div className="text-sm text-slate-300 font-medium">Search instruments</div>
                  <input
                    autoFocus
                    value={searchQ}
                    onChange={e => doSearch(e.target.value)}
                    placeholder="Type ticker or name…"
                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-600"
                  />
                  {searching && <div className="text-xs text-slate-500">Searching…</div>}
                  {searchResults.length > 0 && (
                    <div className="rounded-lg border border-slate-700 overflow-hidden divide-y divide-slate-800">
                      {searchResults.map(inst => (
                        <button
                          key={inst.id}
                          onClick={() => resolve({ action: 'map', instrument_id: inst.id })}
                          disabled={resolving}
                          className="w-full text-left px-3 py-2.5 hover:bg-slate-800 transition-colors"
                        >
                          <span className="text-sm font-mono text-indigo-400">{inst.ticker ?? '—'}</span>
                          <span className="text-xs text-slate-400 ml-2">{inst.name}</span>
                          <span className="text-xs text-slate-600 ml-2">{inst.instrument_type}</span>
                        </button>
                      ))}
                    </div>
                  )}
                  {searchQ.length > 0 && searchResults.length === 0 && !searching && (
                    <div className="text-xs text-slate-500">
                      No match — use <button onClick={() => { setActionMode('create'); setCreateTicker(searchQ.toUpperCase()) }} className="text-indigo-400 hover:underline">Create new instrument</button> instead.
                    </div>
                  )}
                  <button onClick={resetPanel} className="text-xs text-slate-500 hover:text-slate-300">← Back</button>
                </div>
              )}

              {/* Create new */}
              {actionMode === 'create' && (
                <div className="space-y-3">
                  <div className="text-sm text-slate-300 font-medium">Create new instrument</div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="text-xs text-slate-500 block mb-1">Ticker</label>
                      <input
                        autoFocus
                        value={createTicker}
                        onChange={e => setCreateTicker(e.target.value.toUpperCase())}
                        placeholder="e.g. ALL-B"
                        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 font-mono placeholder-slate-500 focus:outline-none focus:border-indigo-600"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-slate-500 block mb-1">Type</label>
                      <select
                        value={createType}
                        onChange={e => setCreateType(e.target.value)}
                        className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 focus:outline-none focus:border-indigo-600"
                      >
                        {INSTRUMENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                      </select>
                    </div>
                  </div>
                  <div>
                    <label className="text-xs text-slate-500 block mb-1">Name</label>
                    <input
                      value={createName}
                      onChange={e => setCreateName(e.target.value)}
                      placeholder="e.g. Allstate Corp 5.100% Preferred Series B"
                      className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-indigo-600"
                    />
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => resolve({ action: 'create', ticker: createTicker, name: createName || createTicker, instrument_type: createType })}
                      disabled={resolving || !createTicker}
                      className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-indigo-700 hover:bg-indigo-600 text-white text-sm disabled:opacity-50"
                    >
                      <CheckCircle size={14} /> {resolving ? 'Saving…' : 'Create & resolve'}
                    </button>
                    <button onClick={resetPanel} className="text-xs text-slate-500 hover:text-slate-300">← Back</button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
