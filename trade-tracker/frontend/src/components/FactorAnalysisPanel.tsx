import { useEffect, useMemo, useState } from 'react'
import { Activity, Grid3X3, RefreshCw } from 'lucide-react'

import { get } from '../api/client'
import type { FactorAnalysis, Period } from '../types'

const BENCHMARKS = ['SPY', 'QQQ', 'IWM', 'DIA'] as const

interface FactorAnalysisPanelProps {
  period: Period
  accountId: string | null
  defaultBenchmark?: string
  refreshSignal?: string
}

function correlationColor(value: number | null) {
  if (value == null) return { backgroundColor: '#f3f6f9', color: '#9aa7b5' }
  const strength = Math.min(Math.abs(value), 1)
  if (value > 0.05) {
    return {
      backgroundColor: `rgba(13, 148, 136, ${0.1 + strength * 0.72})`,
      color: strength > 0.52 ? '#ffffff' : '#115e59',
    }
  }
  if (value < -0.05) {
    return {
      backgroundColor: `rgba(225, 82, 65, ${0.1 + strength * 0.72})`,
      color: strength > 0.52 ? '#ffffff' : '#9f2d22',
    }
  }
  return { backgroundColor: '#edf1f5', color: '#667386' }
}

function betaBand(beta: number | null) {
  if (beta == null) return 'Unavailable'
  if (beta < 0) return 'Inverse'
  if (beta < 0.8) return 'Defensive'
  if (beta <= 1.2) return 'Market-like'
  return 'High sensitivity'
}

export default function FactorAnalysisPanel({
  period,
  accountId,
  defaultBenchmark = 'SPY',
  refreshSignal,
}: FactorAnalysisPanelProps) {
  const normalizedDefault = defaultBenchmark.toUpperCase()
  const [benchmark, setBenchmark] = useState(normalizedDefault)
  const [analysis, setAnalysis] = useState<FactorAnalysis | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [retryKey, setRetryKey] = useState(0)
  const benchmarkOptions = useMemo(
    () => Array.from(new Set([normalizedDefault, ...BENCHMARKS])),
    [normalizedDefault],
  )

  useEffect(() => {
    let cancelled = false
    const params = new URLSearchParams({
      period,
      benchmark,
      max_positions: '6',
    })
    if (accountId) params.set('account_id', accountId)

    setLoading(true)
    setError(null)
    get<FactorAnalysis>(`/portfolio/factor-analysis?${params.toString()}`)
      .then((response) => {
        if (!cancelled) setAnalysis(response)
      })
      .catch((requestError) => {
        if (!cancelled) {
          setAnalysis(null)
          setError(requestError.message)
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [accountId, benchmark, period, refreshSignal, retryKey])

  const beta = analysis?.beta == null ? null : Number(analysis.beta)
  const markerPosition = useMemo(() => {
    if (beta == null) return null
    return Math.max(0, Math.min(100, ((beta + 0.5) / 3) * 100))
  }, [beta])

  return (
    <div data-testid="factor-analysis-panel" className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-3 pb-4">
        <div className="flex items-center gap-2 text-xs font-medium" style={{ color: '#5f6f83' }}>
          <Activity size={15} aria-hidden="true" />
          <span>Daily returns</span>
          <span style={{ color: '#b1bcc8' }}>·</span>
          <span>{period.toUpperCase()}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium" style={{ color: '#667386' }}>Benchmark</span>
          <div
            className="inline-flex overflow-hidden"
            role="group"
            aria-label="Beta benchmark"
            style={{ border: '1px solid #cbd6e2', borderRadius: 6 }}
          >
            {benchmarkOptions.map((symbol, index) => {
              const selected = benchmark === symbol
              return (
                <button
                  key={symbol}
                  type="button"
                  aria-pressed={selected}
                  onClick={() => setBenchmark(symbol)}
                  className="px-3 py-1.5 text-xs font-semibold"
                  style={{
                    backgroundColor: selected ? '#1d4ed8' : '#ffffff',
                    color: selected ? '#ffffff' : '#536277',
                    borderRight: index === benchmarkOptions.length - 1 ? 'none' : '1px solid #d8e1ea',
                  }}
                >
                  {symbol}
                </button>
              )
            })}
          </div>
        </div>
      </div>

      {error ? (
        <div className="flex min-h-48 items-center justify-center gap-3 text-sm" style={{ color: '#9f2d22' }}>
          <span>Factor data unavailable</span>
          <button
            type="button"
            aria-label="Retry factor analysis"
            title="Retry factor analysis"
            onClick={() => setRetryKey((value) => value + 1)}
            className="inline-flex h-8 w-8 items-center justify-center"
            style={{ border: '1px solid #e5b7b0', borderRadius: 6, backgroundColor: '#fff' }}
          >
            <RefreshCw size={15} />
          </button>
        </div>
      ) : (
        <div className="grid min-w-0 gap-0 lg:grid-cols-[250px_minmax(0,1fr)]">
          <section
            className="flex min-h-64 flex-col justify-between border-b pb-5 lg:border-b-0 lg:border-r lg:pb-0 lg:pr-6"
            style={{ borderColor: '#e2e8ef' }}
            aria-label="Portfolio beta"
          >
            <div>
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold uppercase" style={{ color: '#748297', letterSpacing: 0 }}>
                  Portfolio beta
                </span>
                <span
                  className="px-2 py-1 text-xs font-semibold"
                  style={{
                    borderRadius: 4,
                    color: beta != null && beta > 1.2 ? '#9f2d22' : '#0f766e',
                    backgroundColor: beta != null && beta > 1.2 ? '#fff0ed' : '#e7f7f4',
                  }}
                >
                  {betaBand(beta)}
                </span>
              </div>
              <div className="mt-5 flex items-end gap-2">
                <span className="text-5xl font-semibold leading-none tabular-nums" style={{ color: '#17233b' }}>
                  {loading ? '...' : beta == null ? '—' : beta.toFixed(2)}
                </span>
                <span className="pb-1 text-sm font-semibold" style={{ color: '#718096' }}>
                  vs {analysis?.benchmark_symbol ?? benchmark}
                </span>
              </div>
              <div className="mt-6">
                <div className="relative h-2" style={{ borderRadius: 2, backgroundColor: '#dfe6ed' }}>
                  <div
                    className="absolute inset-y-0 left-0"
                    style={{ width: '50%', backgroundColor: '#9fd8cf', borderRadius: 2 }}
                  />
                  <div
                    className="absolute inset-y-0 right-0"
                    style={{ width: '50%', backgroundColor: '#f2b3a9', borderRadius: 2 }}
                  />
                  {markerPosition != null && (
                    <span
                      className="absolute top-1/2 h-4 w-1 -translate-x-1/2 -translate-y-1/2"
                      style={{ left: `${markerPosition}%`, backgroundColor: '#17233b', borderRadius: 1 }}
                    />
                  )}
                </div>
                <div className="mt-2 flex justify-between text-xs tabular-nums" style={{ color: '#8794a6' }}>
                  <span>-0.5</span>
                  <span>1.0</span>
                  <span>2.5</span>
                </div>
              </div>
            </div>
            <div className="mt-6 flex items-end justify-between border-t pt-4" style={{ borderColor: '#edf1f5' }}>
              <div>
                <div className="text-xs" style={{ color: '#8794a6' }}>Observations</div>
                <div className="mt-1 text-lg font-semibold tabular-nums" style={{ color: '#263650' }}>
                  {loading ? '...' : analysis?.beta_observations ?? 0}
                </div>
              </div>
              <div className="text-right text-xs leading-5" style={{ color: '#8794a6' }}>
                <div>OLS slope</div>
                <div>Daily frequency</div>
              </div>
            </div>
          </section>

          <section className="min-w-0 pt-5 lg:pl-6 lg:pt-0" aria-label="Correlation matrix">
            <div className="mb-3 flex items-center justify-between gap-3">
              <div className="flex items-center gap-2">
                <Grid3X3 size={15} style={{ color: '#536277' }} aria-hidden="true" />
                <span className="text-xs font-semibold uppercase" style={{ color: '#748297', letterSpacing: 0 }}>
                  Correlation matrix
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs tabular-nums" style={{ color: '#8794a6' }}>
                <span className="h-2.5 w-2.5" style={{ backgroundColor: '#df6656', borderRadius: 2 }} />
                <span>-1</span>
                <span className="h-2.5 w-2.5" style={{ backgroundColor: '#edf1f5', borderRadius: 2 }} />
                <span>0</span>
                <span className="h-2.5 w-2.5" style={{ backgroundColor: '#148f85', borderRadius: 2 }} />
                <span>+1</span>
              </div>
            </div>

            <div className="overflow-x-auto pb-1">
              {loading ? (
                <div className="flex min-h-52 items-center justify-center text-sm" style={{ color: '#9aa7b5' }}>
                  Calculating factors...
                </div>
              ) : !analysis?.series.length ? (
                <div className="flex min-h-52 items-center justify-center text-sm" style={{ color: '#9aa7b5' }}>
                  No return series available
                </div>
              ) : (
                <table className="w-full min-w-[620px] table-fixed border-separate" style={{ borderSpacing: 3 }}>
                  <thead>
                    <tr>
                      <th className="w-28 px-2 text-left text-xs font-medium" style={{ color: '#8794a6' }}>
                        Series
                      </th>
                      {analysis.series.map((series) => (
                        <th
                          key={series.symbol}
                          className="h-9 px-1 text-center text-xs font-semibold"
                          style={{ color: series.kind === 'benchmark' ? '#1d4ed8' : '#536277' }}
                          scope="col"
                        >
                          {series.symbol === 'PORTFOLIO' ? 'PF' : series.label}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {analysis.series.map((row, rowIndex) => (
                      <tr key={row.symbol}>
                        <th className="h-12 px-2 text-left" scope="row">
                          <div className="text-xs font-semibold" style={{ color: '#34445e' }}>
                            {row.symbol === 'PORTFOLIO' ? 'Portfolio' : row.label}
                          </div>
                          {row.kind === 'position' && row.portfolio_weight_pct != null && (
                            <div className="mt-0.5 text-xs font-normal tabular-nums" style={{ color: '#9aa7b5' }}>
                              {Number(row.portfolio_weight_pct).toFixed(1)}%
                            </div>
                          )}
                        </th>
                        {analysis.series.map((column, columnIndex) => {
                          const value = analysis.correlations[rowIndex]?.[columnIndex] ?? null
                          const observations = analysis.correlation_observations[rowIndex]?.[columnIndex] ?? 0
                          return (
                            <td
                              key={column.symbol}
                              className="h-12 text-center text-xs font-semibold tabular-nums"
                              style={{ ...correlationColor(value), borderRadius: 4 }}
                              title={`${row.label} / ${column.label}: ${value == null ? 'n/a' : value.toFixed(2)} (${observations} observations)`}
                            >
                              {value == null ? '—' : value.toFixed(2)}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </section>
        </div>
      )}
    </div>
  )
}
