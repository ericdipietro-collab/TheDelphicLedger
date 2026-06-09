import { useState, useCallback, useRef } from 'react'
import { Upload, CheckCircle, AlertCircle, FileText, ChevronDown, ChevronUp } from 'lucide-react'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ColumnProposal {
  source_col: string
  canonical_field: string | null
  method: string
  score: number | null
}

interface StageResult {
  file_hash: string
  filename: string
  file_type: string
  row_count: number
  template_name: string | null
  known_template: boolean
  column_map: Record<string, string | null>
  proposals: ColumnProposal[]
  queued_types: string[]
}

interface ConfirmResult {
  batch_id: number
  row_count: number
  file_type: string
  resolved_count: number
  queued_count: number
  template_saved: string | null
}

// ── Canonical fields the user can map columns to ──────────────────────────────

const CANONICAL_FIELDS = [
  'symbol', 'name', 'qty', 'price', 'market_value', 'cost_basis', 'as_of',
  'trade_date', 'settle_date', 'raw_type', 'amount', 'fees',
  'distribution_yield', 'asset_class',
]

// ── API helpers ────────────────────────────────────────────────────────────────

async function stageFile(file: File): Promise<StageResult> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch('/api/ingest/stage', { method: 'POST', body: form })
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail ?? res.statusText)
  return body as StageResult
}

async function confirmImport(
  file_hash: string,
  column_map: Record<string, string | null>,
  save_as_template: string | null,
): Promise<ConfirmResult> {
  const res = await fetch('/api/ingest/confirm', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ file_hash, column_map, save_as_template: save_as_template || null }),
  })
  const body = await res.json()
  if (!res.ok) throw new Error(body.detail ?? res.statusText)
  return body as ConfirmResult
}

// ── Method badge ──────────────────────────────────────────────────────────────

function MethodBadge({ method, score }: { method: string; score: number | null }) {
  const styles: Record<string, string> = {
    exact: 'bg-emerald-950/40 text-emerald-400 border-emerald-900/40',
    fuzzy_auto: 'bg-blue-950/40 text-blue-400 border-blue-900/40',
    fuzzy_pending: 'bg-amber-950/40 text-amber-400 border-amber-900/40',
    unmatched: 'bg-slate-800 text-slate-500 border-slate-700',
  }
  const label: Record<string, string> = {
    exact: 'exact', fuzzy_auto: `fuzzy ${score?.toFixed(0)}`, fuzzy_pending: `review ${score?.toFixed(0)}`, unmatched: 'unmatched',
  }
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-mono ${styles[method] ?? styles.unmatched}`}>
      {label[method] ?? method}
    </span>
  )
}

// ── Main component ─────────────────────────────────────────────────────────────

export function Import() {
  const [stage, setStage] = useState<'idle' | 'staging' | 'review' | 'confirming' | 'done'>('idle')
  const [staged, setStaged] = useState<StageResult | null>(null)
  const [columnMap, setColumnMap] = useState<Record<string, string | null>>({})
  const [templateName, setTemplateName] = useState('')
  const [result, setResult] = useState<ConfirmResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [mappingExpanded, setMappingExpanded] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.toLowerCase().endsWith('.csv')) {
      setError('Only CSV files are supported.')
      return
    }
    setError(null)
    setStage('staging')
    try {
      const s = await stageFile(file)
      setStaged(s)
      setColumnMap(s.column_map)
      setStage('review')
    } catch (e) {
      setError(String(e))
      setStage('idle')
    }
  }, [])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  const handleConfirm = async () => {
    if (!staged) return
    setStage('confirming')
    setError(null)
    try {
      const r = await confirmImport(staged.file_hash, columnMap, templateName.trim() || null)
      setResult(r)
      setStage('done')
    } catch (e) {
      setError(String(e))
      setStage('review')
    }
  }

  const reset = () => {
    setStage('idle')
    setStaged(null)
    setColumnMap({})
    setTemplateName('')
    setResult(null)
    setError(null)
    setMappingExpanded(false)
  }

  return (
    <div className="space-y-8 max-w-3xl">
      <div>
        <h1 className="text-2xl font-semibold text-white">Import</h1>
        <p className="text-sm text-slate-500 mt-1">Upload a broker CSV to import positions or transactions.</p>
      </div>

      {error && (
        <div className="flex items-start gap-2.5 px-4 py-3 rounded-lg bg-rose-950/30 border border-rose-900/40 text-sm text-rose-400">
          <AlertCircle size={15} className="flex-shrink-0 mt-0.5" />
          <span className="font-mono text-xs break-all">{error}</span>
        </div>
      )}

      {/* ── Done state ── */}
      {stage === 'done' && result && (
        <div className="rounded-xl border border-emerald-900/40 bg-emerald-950/20 p-6 space-y-4">
          <div className="flex items-center gap-3">
            <CheckCircle size={22} className="text-emerald-400 flex-shrink-0" />
            <div>
              <p className="font-semibold text-emerald-300">Import complete</p>
              <p className="text-sm text-slate-500 mt-0.5">Batch #{result.batch_id}</p>
            </div>
          </div>
          <div className="grid grid-cols-3 gap-4 text-center">
            {[
              { label: 'Rows imported', value: result.row_count, color: 'text-slate-200' },
              { label: 'Instruments resolved', value: result.resolved_count, color: 'text-emerald-400' },
              { label: 'Queued for review', value: result.queued_count, color: result.queued_count > 0 ? 'text-amber-400' : 'text-slate-600' },
            ].map(s => (
              <div key={s.label} className="rounded-lg bg-slate-900 border border-slate-800 py-3">
                <p className={`text-2xl font-bold font-mono tabular ${s.color}`}>{s.value}</p>
                <p className="text-xs text-slate-600 mt-0.5">{s.label}</p>
              </div>
            ))}
          </div>
          {result.queued_count > 0 && (
            <p className="text-xs text-amber-500/80">
              {result.queued_count} instrument{result.queued_count !== 1 ? 's' : ''} couldn't be auto-resolved.
              Run <code className="text-amber-400 font-mono">committee resolve</code> to review them.
            </p>
          )}
          {result.template_saved && (
            <p className="text-xs text-emerald-500/80">
              Format saved as <span className="font-mono text-emerald-400">"{result.template_saved}"</span> — next upload from this broker will skip the mapping step.
            </p>
          )}
          <button onClick={reset} className="text-sm text-slate-500 hover:text-slate-300 underline underline-offset-2">
            Import another file
          </button>
        </div>
      )}

      {/* ── Drop zone ── */}
      {(stage === 'idle' || stage === 'staging') && (
        <div
          onDrop={onDrop}
          onDragOver={e => { e.preventDefault(); setDragging(true) }}
          onDragLeave={() => setDragging(false)}
          onClick={() => inputRef.current?.click()}
          className={`flex flex-col items-center justify-center gap-4 rounded-2xl border-2 border-dashed p-16 cursor-pointer transition-colors ${
            dragging
              ? 'border-blue-500 bg-blue-500/5'
              : 'border-slate-700 hover:border-slate-500 bg-slate-900/50'
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            accept=".csv"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
          {stage === 'staging' ? (
            <>
              <div className="w-10 h-10 rounded-full border-2 border-blue-500 border-t-transparent animate-spin" />
              <p className="text-slate-500 text-sm">Detecting format…</p>
            </>
          ) : (
            <>
              <Upload size={28} className="text-slate-600" />
              <div className="text-center">
                <p className="text-slate-300 font-medium">Drop a CSV here</p>
                <p className="text-slate-600 text-sm mt-1">or click to browse · positions or transactions</p>
              </div>
            </>
          )}
        </div>
      )}

      {/* ── Review panel ── */}
      {(stage === 'review' || stage === 'confirming') && staged && (
        <div className="space-y-5">
          {/* File summary */}
          <div className="flex items-start gap-4 p-5 rounded-xl border border-slate-800 bg-slate-900">
            <FileText size={20} className="text-slate-500 flex-shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="font-medium text-slate-200 truncate">{staged.filename}</p>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mt-1">
                <span className="text-xs text-slate-500 font-mono">
                  {staged.row_count} {staged.file_type} rows
                </span>
                {staged.template_name ? (
                  <span className="text-xs px-2 py-0.5 rounded bg-emerald-950/40 text-emerald-400 border border-emerald-900/40 font-mono">
                    template: {staged.template_name}
                  </span>
                ) : (
                  <span className="text-xs px-2 py-0.5 rounded bg-amber-950/40 text-amber-400 border border-amber-900/40 font-mono">
                    new format — review mapping
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Column mapping — collapsed by default if template is known */}
          <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
            <button
              className="w-full flex items-center justify-between px-5 py-3 text-left hover:bg-slate-800/40 transition-colors"
              onClick={() => setMappingExpanded(v => !v)}
            >
              <span className="text-xs font-medium uppercase tracking-wider text-slate-500">
                Column mapping
                {!staged.known_template && <span className="ml-2 text-amber-400">— review before confirming</span>}
              </span>
              {mappingExpanded ? <ChevronUp size={14} className="text-slate-600" /> : <ChevronDown size={14} className="text-slate-600" />}
            </button>

            {(mappingExpanded || !staged.known_template) && (
              <div className="border-t border-slate-800">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-slate-800">
                      <th className="text-left px-5 py-2.5 text-slate-500 font-medium">CSV column</th>
                      <th className="text-left px-5 py-2.5 text-slate-500 font-medium">Maps to</th>
                      <th className="px-5 py-2.5 text-slate-500 font-medium">Confidence</th>
                    </tr>
                  </thead>
                  <tbody>
                    {staged.proposals.map(p => (
                      <tr key={p.source_col} className="border-b border-slate-800/50">
                        <td className="px-5 py-2.5 font-mono text-slate-300">{p.source_col}</td>
                        <td className="px-5 py-2.5">
                          <select
                            value={columnMap[p.source_col] ?? ''}
                            onChange={e => setColumnMap(prev => ({
                              ...prev,
                              [p.source_col]: e.target.value || null,
                            }))}
                            className="bg-slate-800 border border-slate-700 text-slate-200 text-xs rounded-lg px-2 py-1 focus:outline-none font-mono w-full max-w-[180px]"
                          >
                            <option value="">(skip)</option>
                            {CANONICAL_FIELDS.map(f => (
                              <option key={f} value={f}>{f}</option>
                            ))}
                          </select>
                        </td>
                        <td className="px-5 py-2.5 text-center">
                          <MethodBadge method={p.method} score={p.score} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          {staged.queued_types.length > 0 && (
            <div className="px-4 py-2.5 rounded-lg bg-amber-950/20 border border-amber-900/30 text-xs text-amber-400/80">
              Unknown transaction types will be queued: {staged.queued_types.map(t => (
                <code key={t} className="mx-0.5 text-amber-300 font-mono">{t}</code>
              ))}
            </div>
          )}

          {/* Save template — only shown for unknown formats */}
          {!staged.known_template && (
            <div className="flex items-center gap-3 px-4 py-3 rounded-lg border border-slate-800 bg-slate-900/60">
              <input
                type="text"
                placeholder="Save format as… (e.g. Fidelity Positions)"
                value={templateName}
                onChange={e => setTemplateName(e.target.value)}
                className="flex-1 bg-transparent border-none text-sm text-slate-300 placeholder-slate-600 focus:outline-none font-mono"
              />
              {templateName.trim() && (
                <span className="text-xs text-emerald-500 whitespace-nowrap">will save on confirm</span>
              )}
            </div>
          )}

          <div className="flex items-center gap-3">
            <button
              onClick={handleConfirm}
              disabled={stage === 'confirming'}
              className="px-5 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm rounded-lg font-medium transition-colors"
            >
              {stage === 'confirming' ? 'Importing…' : 'Confirm import'}
            </button>
            <button
              onClick={reset}
              disabled={stage === 'confirming'}
              className="text-sm text-slate-500 hover:text-slate-300 disabled:opacity-40"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Guidance note */}
      {stage === 'idle' && (
        <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-xs text-slate-600 space-y-1.5 leading-relaxed">
          <p><span className="text-slate-400 font-medium">Positions CSV</span> — one row per holding with symbol, quantity, price or market value, and a date column.</p>
          <p><span className="text-slate-400 font-medium">Transactions CSV</span> — one row per trade with date, type (buy/sell/dividend), symbol, quantity, and amount.</p>
          <p>Known broker formats are recognized automatically. New formats show a column-mapping step.</p>
          <p className="text-slate-700">Source records are immutable after import — no edits or deletes, only new batches.</p>
        </div>
      )}
    </div>
  )
}
