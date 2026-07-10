// Pure view-model math for the paper-execution surface (EXEC P3c). No React,
// no IO — exercised directly by vitest.
//
// Column semantics mirror the backend tracker: optimistic = exact honest
// anchor/barriers; conservative = 1-tick-adverse entry (and 1-tick-adverse sl
// exit on realized closes). Unrealized marks apply the entry adjustment only —
// a print is a print, so the mark itself is never adjusted.

import { PRICE_TICK_SIZE } from '../chart/viewModels';
import type { MarketBar, OpenPosition } from '../domain/models';

export type LastPrice = { price: number; timeUtc: string };

// The newest observed trade print, exactly as the chart sees it: the close of
// the most recently closing bar (forming bars carry the very latest trade).
export const latestPrice = (currentBars: MarketBar[], recentClosedBars: MarketBar[]): LastPrice | null => {
  let best: MarketBar | null = null;
  for (const bar of [...recentClosedBars, ...currentBars]) {
    if (!Number.isFinite(bar.closeTicks)) continue;
    if (best === null || Date.parse(bar.closeTimeUtc) >= Date.parse(best.closeTimeUtc)) best = bar;
  }
  return best === null ? null : { price: best.closeTicks * PRICE_TICK_SIZE, timeUtc: best.closeTimeUtc };
};

export type UnrealizedView = {
  points: number | null;
  pointsConservative: number | null;
  markPrice: number | null;
};

// Live unrealized P&L (both columns) against the latest local print; falls
// back to the values the backend stamped on the DTO when no bar has arrived
// on this client yet (e.g. right after a snapshot).
export const unrealizedFor = (position: OpenPosition, last: LastPrice | null): UnrealizedView => {
  if (last === null) {
    return {
      points: position.unrealizedPoints,
      pointsConservative: position.unrealizedPointsConservative,
      markPrice: position.lastPrice,
    };
  }
  const sign = position.direction === 'short' ? -1 : 1;
  return {
    points: (last.price - position.entryPrice) * sign,
    pointsConservative: (last.price - position.entryPriceConservative) * sign,
    markPrice: last.price,
  };
};

export const formatSignedPoints = (value: number | null, digits = 2): string => {
  if (value === null || Number.isNaN(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(digits)}`;
};

// Position age on the EVENT clock (latest observed bar close), not the wall
// clock — replay positions age at replay speed and a paused feed stops aging.
export const formatAge = (entryTsUtc: string, nowUtc: string | null): string => {
  if (!nowUtc) return '—';
  const ms = Date.parse(nowUtc) - Date.parse(entryTsUtc);
  if (!Number.isFinite(ms) || ms < 0) return '—';
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}h ${minutes.toString().padStart(2, '0')}m`;
  if (minutes > 0) return `${minutes}m ${seconds.toString().padStart(2, '0')}s`;
  return `${seconds}s`;
};

// UTC wall-clock HH:MM:SS for compact table cells.
export const formatClock = (iso: string | null): string => {
  if (!iso) return '—';
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return '—';
  return new Date(ms).toISOString().slice(11, 19);
};
