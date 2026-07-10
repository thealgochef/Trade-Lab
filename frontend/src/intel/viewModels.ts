// COCKPIT P3: pure view-model math for the decision surfaces — distance-sorted
// levels, the prediction gate math, and the active-setup card. No React, no IO.
import type { MarketLevel, MarketTouch, ModelStatus, Observation, Prediction } from '../domain/models';

const TICKS_PER_POINT = 4;

// Fallback presentation order when no last price exists yet (the pre-COCKPIT
// fixed ordering).
const LEVEL_ORDER = ['pdh', 'pdl', 'asia_high', 'asia_low', 'london_high', 'london_low', 'ny_high', 'ny_low'];

export type LevelDistanceRow = {
  level: MarketLevel;
  /** Signed ticks from last price to the level (positive = level above). */
  distanceTicks: number | null;
  nearest: boolean;
};

/**
 * Levels sorted by absolute distance from the last price (nearest first, the
 * nearest row flagged). Without a last price, falls back to the fixed kind
 * order with null distances.
 */
export function sortLevelsByDistance(levels: MarketLevel[], lastPriceTicks: number | null): LevelDistanceRow[] {
  if (lastPriceTicks === null) {
    return [...levels]
      .sort((a, b) => LEVEL_ORDER.indexOf(a.kind) - LEVEL_ORDER.indexOf(b.kind))
      .map((level) => ({ level, distanceTicks: null, nearest: false }));
  }
  const rows = levels.map((level) => ({
    level,
    distanceTicks: level.priceTicks - lastPriceTicks,
    nearest: false,
  }));
  rows.sort((a, b) => {
    const byDistance = Math.abs(a.distanceTicks as number) - Math.abs(b.distanceTicks as number);
    if (byDistance !== 0) return byDistance;
    return LEVEL_ORDER.indexOf(a.level.kind) - LEVEL_ORDER.indexOf(b.level.kind);
  });
  if (rows.length > 0) rows[0] = { ...rows[0], nearest: true };
  return rows;
}

/** Signed distance as "+12.50 pts (+50t)"; em-dash without a last price. */
export function formatLevelDistance(distanceTicks: number | null): string {
  if (distanceTicks === null) return '—';
  const points = distanceTicks / TICKS_PER_POINT;
  const sign = distanceTicks > 0 ? '+' : distanceTicks < 0 ? '−' : '';
  return `${sign}${Math.abs(points).toFixed(2)} pts (${sign}${Math.abs(distanceTicks)}t)`;
}

/**
 * Honest origin label for a level: the carried origin session when present;
 * PDH/PDL are prior-day levels by definition (the adapter maps them to a null
 * origin_session — see COCKPIT P1b); anything else is genuinely unknown.
 */
export function levelOriginLabel(level: MarketLevel | undefined): string {
  if (!level) return 'unknown';
  if (level.originSession) return level.originSession;
  return level.kind === 'pdh' || level.kind === 'pdl' ? 'prior day' : 'unknown';
}

export type GateMath = {
  eligibleClass: string;
  gate: number;
  /** Probability the model assigned to the eligible class (0 when absent). */
  probability: number;
  passes: boolean;
  /**
   * WHY the prediction is ineligible, in priority order: class (predicted the
   * wrong class), session (outside the eligible sessions), gate (eligible-class
   * probability below the confidence gate). Null when eligible or unexplainable
   * from the shipped parameters (trust the backend verdict either way).
   */
  reason: 'class' | 'session' | 'gate' | null;
};

/**
 * The serving-gate math for one prediction against the ACTIVE model's gate
 * parameters. Returns null when the parameters are unavailable or the
 * prediction came from a different model than the one now active (a hot-swap
 * makes old rows unjudgeable against the current gate).
 */
export function gateMath(prediction: Prediction, modelStatus: ModelStatus | null): GateMath | null {
  if (!modelStatus || !modelStatus.loaded) return null;
  if (modelStatus.confidenceGate === null || modelStatus.eligibleClass === null) return null;
  if (modelStatus.modelId !== null && prediction.modelId !== modelStatus.modelId) return null;
  const gate = modelStatus.confidenceGate;
  const eligibleClass = modelStatus.eligibleClass;
  const probability = prediction.probabilities[eligibleClass] ?? 0;
  let reason: GateMath['reason'] = null;
  if (!prediction.eligible) {
    if (prediction.predictedClass !== eligibleClass) {
      reason = 'class';
    } else if (modelStatus.eligibleSessions !== null && !modelStatus.eligibleSessions.includes(prediction.session)) {
      reason = 'session';
    } else if (probability < gate) {
      reason = 'gate';
    }
  }
  return { eligibleClass, gate, probability, passes: prediction.eligible, reason };
}

export function describeGateReason(math: GateMath, prediction: Prediction, modelStatus: ModelStatus | null): string | null {
  switch (math.reason) {
    case 'class':
      return `class — predicted ${prediction.predictedClass}, eligible ${math.eligibleClass}`;
    case 'session': {
      const sessions = modelStatus?.eligibleSessions?.join(', ') ?? '';
      return `session — ${prediction.session} not in [${sessions}]`;
    }
    case 'gate':
      return `gate — ${math.probability.toFixed(2)} below ${math.gate.toFixed(2)}`;
    default:
      return null;
  }
}

export type ActiveSetup = {
  observation: Observation;
  /** Touch (trade) price in ticks from the originating touch, if still buffered. */
  touchPriceTicks: number | null;
  activeCount: number;
};

/**
 * The newest ACTIVE observation joined to its originating touch. The touches
 * ring is bounded and absent from reconnect snapshots, so the join may miss —
 * the touch price is then null, never guessed.
 */
export function activeSetup(observations: Observation[], touches: MarketTouch[]): ActiveSetup | null {
  const active = observations.filter((obs) => obs.status === 'active');
  if (active.length === 0) return null;
  const newest = [...active].sort((a, b) => Date.parse(b.startUtc) - Date.parse(a.startUtc))[0];
  const touch = newest.originatingTouchId === null ? undefined : touches.find((entry) => entry.id === newest.originatingTouchId);
  return {
    observation: newest,
    touchPriceTicks: touch?.priceTicks ?? null,
    activeCount: active.length,
  };
}

/**
 * Seconds until the observation window ends on the EVENT clock (the latest
 * observed print's timestamp — replay countdowns run at replay speed and a
 * paused feed freezes). Null without an event clock; clamps at 0 once the end
 * has been reached (the observation expires on the next processed trade).
 */
export function observationSecondsRemaining(scheduledEndUtc: string, eventClockUtc: string | null): number | null {
  if (!eventClockUtc) return null;
  const end = Date.parse(scheduledEndUtc);
  const now = Date.parse(eventClockUtc);
  if (!Number.isFinite(end) || !Number.isFinite(now)) return null;
  return Math.max(0, Math.round((end - now) / 1000));
}

export function formatSetupCountdown(seconds: number | null): string {
  if (seconds === null) return '—';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
