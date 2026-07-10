import { describe, expect, it } from 'vitest';
import type { MarketLevel, MarketTouch, ModelStatus, Observation, Prediction } from '../domain/models';
import {
  activeSetup,
  describeGateReason,
  formatLevelDistance,
  formatSetupCountdown,
  gateMath,
  levelOriginLabel,
  observationSecondsRemaining,
  sortLevelsByDistance,
} from './viewModels';

const level = (overrides: Partial<MarketLevel>): MarketLevel => ({
  kind: 'pdh',
  priceTicks: 76_000,
  tradingDay: '2026-05-21',
  originSession: null,
  developing: false,
  eligible: true,
  ...overrides,
});

const prediction = (overrides: Partial<Prediction> = {}): Prediction => ({
  id: 'pred-1',
  touchId: 'touch-1',
  observationId: 'obs-1',
  timeUtc: '2026-05-21T14:00:10Z',
  predictedClass: 'up',
  probabilities: { down: 0.1, hold: 0.3, up: 0.6 },
  levelKind: 'pdh',
  levelPriceTicks: 76_000,
  direction: 'long',
  session: 'ny',
  eligible: true,
  modelId: 'model-alpha',
  contractId: 'contract-1',
  nanCount: 0,
  outcome: null,
  dropped: null,
  ...overrides,
});

const modelStatus = (overrides: Partial<ModelStatus> = {}): ModelStatus => ({
  loaded: true,
  modelId: 'model-alpha',
  strategyId: 'strategy-1',
  trainingMode: 'mae_first',
  instrument: 'NQ',
  featureNames: [],
  classMap: {},
  validationOk: true,
  validationDetail: null,
  confidenceGate: 0.7,
  eligibleClass: 'up',
  eligibleSessions: ['ny'],
  ...overrides,
});

const observation = (overrides: Partial<Observation> = {}): Observation => ({
  id: 'obs-1',
  status: 'active',
  session: 'ny',
  levelKind: 'pdh',
  startUtc: '2026-05-21T14:00:00Z',
  scheduledEndUtc: '2026-05-21T14:05:00Z',
  originatingTouchId: 'touch-1',
  levelPriceTicks: 76_000,
  direction: 'short',
  ...overrides,
});

const touch = (overrides: Partial<MarketTouch> = {}): MarketTouch => ({
  id: 'touch-1',
  timeUtc: '2026-05-21T14:00:00Z',
  session: 'ny',
  levelKind: 'pdh',
  priceTicks: 76_004,
  createdObservation: true,
  ...overrides,
});

describe('sortLevelsByDistance', () => {
  it('sorts by absolute distance and flags the nearest row', () => {
    const levels = [
      level({ kind: 'asia_low', priceTicks: 75_600 }),
      level({ kind: 'pdh', priceTicks: 76_020 }),
      level({ kind: 'pdl', priceTicks: 75_900 }),
    ];
    const rows = sortLevelsByDistance(levels, 76_000);
    expect(rows.map((row) => row.level.kind)).toEqual(['pdh', 'pdl', 'asia_low']);
    expect(rows[0].nearest).toBe(true);
    expect(rows[0].distanceTicks).toBe(20);
    expect(rows[1].nearest).toBe(false);
    expect(rows[1].distanceTicks).toBe(-100);
  });

  it('falls back to the fixed kind order without a last price', () => {
    const levels = [
      level({ kind: 'asia_high', priceTicks: 75_600 }),
      level({ kind: 'pdh', priceTicks: 76_020 }),
    ];
    const rows = sortLevelsByDistance(levels, null);
    expect(rows.map((row) => row.level.kind)).toEqual(['pdh', 'asia_high']);
    expect(rows.every((row) => row.distanceTicks === null && !row.nearest)).toBe(true);
  });

  it('formats signed distances in points and ticks', () => {
    expect(formatLevelDistance(50)).toBe('+12.50 pts (+50t)');
    expect(formatLevelDistance(-13)).toBe('−3.25 pts (−13t)');
    expect(formatLevelDistance(0)).toBe('0.00 pts (0t)');
    expect(formatLevelDistance(null)).toBe('—');
  });
});

describe('levelOriginLabel', () => {
  it('prefers the carried origin session, calls PDH/PDL prior day, else unknown', () => {
    expect(levelOriginLabel(level({ kind: 'asia_high', originSession: 'asia' }))).toBe('asia');
    expect(levelOriginLabel(level({ kind: 'pdh', originSession: null }))).toBe('prior day');
    expect(levelOriginLabel(level({ kind: 'pdl', originSession: null }))).toBe('prior day');
    expect(levelOriginLabel(level({ kind: 'asia_high', originSession: null }))).toBe('unknown');
    expect(levelOriginLabel(undefined)).toBe('unknown');
  });
});

describe('gateMath', () => {
  it('reports the eligible-class probability against the gate for a pass', () => {
    const math = gateMath(prediction({ probabilities: { up: 0.82, hold: 0.18 } }), modelStatus());
    expect(math).toEqual({ eligibleClass: 'up', gate: 0.7, probability: 0.82, passes: true, reason: null });
  });

  it('derives WHY in priority order: class before session before gate', () => {
    const status = modelStatus();
    const classWhy = gateMath(
      prediction({ eligible: false, predictedClass: 'hold', session: 'asia', probabilities: { up: 0.1, hold: 0.9 } }),
      status,
    );
    expect(classWhy?.reason).toBe('class');

    const sessionWhy = gateMath(
      prediction({ eligible: false, session: 'asia', probabilities: { up: 0.9 } }),
      status,
    );
    expect(sessionWhy?.reason).toBe('session');

    const gateWhy = gateMath(prediction({ eligible: false, probabilities: { up: 0.69 } }), status);
    expect(gateWhy?.reason).toBe('gate');
    expect(gateWhy?.probability).toBeCloseTo(0.69);
  });

  it('returns null without gate parameters, unloaded status, or a model mismatch', () => {
    expect(gateMath(prediction(), null)).toBeNull();
    expect(gateMath(prediction(), modelStatus({ loaded: false }))).toBeNull();
    expect(gateMath(prediction(), modelStatus({ confidenceGate: null }))).toBeNull();
    expect(gateMath(prediction({ modelId: 'older-model' }), modelStatus())).toBeNull();
  });

  it('describes each reason for the card', () => {
    const status = modelStatus();
    const classWhy = gateMath(prediction({ eligible: false, predictedClass: 'hold' }), status);
    expect(describeGateReason(classWhy!, prediction({ predictedClass: 'hold' }), status)).toBe(
      'class — predicted hold, eligible up',
    );
    const sessionWhy = gateMath(prediction({ eligible: false, session: 'asia', probabilities: { up: 0.9 } }), status);
    expect(describeGateReason(sessionWhy!, prediction({ session: 'asia' }), status)).toBe('session — asia not in [ny]');
    const gateWhy = gateMath(prediction({ eligible: false, probabilities: { up: 0.69 } }), status);
    expect(describeGateReason(gateWhy!, prediction(), status)).toBe('gate — 0.69 below 0.70');
  });
});

describe('activeSetup', () => {
  it('joins the newest active observation to its originating touch', () => {
    const older = observation({ id: 'obs-old', startUtc: '2026-05-21T13:00:00Z', originatingTouchId: 'touch-old' });
    const setup = activeSetup([older, observation()], [touch()]);
    expect(setup?.observation.id).toBe('obs-1');
    expect(setup?.touchPriceTicks).toBe(76_004);
    expect(setup?.activeCount).toBe(2);
  });

  it('returns null with no active observation and survives a missed touch join', () => {
    expect(activeSetup([observation({ status: 'expired' })], [touch()])).toBeNull();
    const setup = activeSetup([observation({ originatingTouchId: 'gone' })], [touch()]);
    expect(setup?.touchPriceTicks).toBeNull();
  });
});

describe('observation countdown (event clock)', () => {
  it('measures remaining seconds on the event clock and clamps at zero', () => {
    expect(observationSecondsRemaining('2026-05-21T14:05:00Z', '2026-05-21T14:00:10Z')).toBe(290);
    expect(observationSecondsRemaining('2026-05-21T14:05:00Z', '2026-05-21T14:06:00Z')).toBe(0);
    expect(observationSecondsRemaining('2026-05-21T14:05:00Z', null)).toBeNull();
  });

  it('formats as m:ss', () => {
    expect(formatSetupCountdown(290)).toBe('4:50');
    expect(formatSetupCountdown(0)).toBe('0:00');
    expect(formatSetupCountdown(null)).toBe('—');
  });
});
