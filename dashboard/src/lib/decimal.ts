/**
 * Decimal helpers for authoritative financial values.
 *
 * Rules (FR-5):
 * - Parse from string ONLY (never from JS number).
 * - All arithmetic via Big (never native JS arithmetic operators).
 * - Rounding: ROUND_HALF_EVEN (Big.RM = 2), scale = 4.
 * - toChartNumber() is the ONLY approved Big-to-number conversion point.
 * - Values from toChartNumber() MUST NOT re-enter authoritative calculations.
 */

import Big from 'big.js'

Big.RM = 2  // ROUND_HALF_EVEN

export type DecimalStr = string

/** Parse a backend decimal string to Big. Throws on null/undefined/'n/a'. */
export function parse(s: DecimalStr | null | undefined): Big {
  if (!s || s === 'None' || s === 'n/a') throw new Error(`Cannot parse decimal: ${s}`)
  return new Big(s)
}

/** Parse, returning Big(0) on null/undefined/'n/a'. Safe for totals. */
export function parseSafe(s: DecimalStr | null | undefined): Big {
  if (!s || s === 'None' || s === 'n/a') return new Big(0)
  return new Big(s)
}

export const add = (a: Big, b: Big): Big => a.plus(b)
export const sub = (a: Big, b: Big): Big => a.minus(b)
export const mul = (a: Big, b: Big): Big => a.times(b)
export const div = (a: Big, b: Big): Big => a.div(b)
export const gt = (a: Big, b: Big): boolean => a.gt(b)
export const lt = (a: Big, b: Big): boolean => a.lt(b)
export const eq = (a: Big, b: Big): boolean => a.eq(b)

/** Round to authoritative scale (4dp, ROUND_HALF_EVEN). */
export function round4(v: Big): Big {
  return v.round(4)
}

/** Format as USD currency string. Display-only. */
export function fmtCurrency(v: Big): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(v.toNumber())
}

/** Format as percentage string. Display-only. */
export function fmtPercent(v: Big, decimals = 1): string {
  return `${v.times(100).round(decimals).toFixed(decimals)}%`
}

/**
 * Display adapter: convert Big → JS number for chart libraries ONLY.
 * The returned number MUST NOT re-enter any authoritative calculation.
 */
export function toChartNumber(v: Big): number {
  return v.toNumber()
}
