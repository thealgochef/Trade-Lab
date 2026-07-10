import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { apiClient } from '../api/client';
import type { PerformanceKeyRowDTO, PerformanceReportDTO } from '../api/types';
import {
  DEFAULT_DOLLARS_PER_POINT,
  buildCumulativeCurve,
  buildDailyBars,
  buildMonthCalendar,
  buildStatCards,
  calendarCellColor,
  formatDuration,
  formatPoints,
  formatRatio,
  monthsWithData,
  parseMonthKey,
  seriesByDay,
  shiftMonth,
} from '../performance/viewModels';

type Mode = 'all' | 'replay' | 'live';
type BundleScope = 'active' | 'all';

export function PerformancePage() {
  const [report, setReport] = useState<PerformanceReportDTO | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [noJournal, setNoJournal] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>('all');
  const [fromDay, setFromDay] = useState('');
  const [toDay, setToDay] = useState('');
  const [showDollars, setShowDollars] = useState(false);
  const [activeBundleId, setActiveBundleId] = useState<string | null>(null);
  const [bundleScope, setBundleScope] = useState<BundleScope>('active');
  const [monthCursor, setMonthCursor] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void apiClient.activeModel().then((result) => {
      if (cancelled) return;
      if (result.ok && result.data.loaded && result.data.model_id) {
        setActiveBundleId(result.data.model_id);
      } else {
        setActiveBundleId(null);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const bundleParam = bundleScope === 'active' && activeBundleId ? activeBundleId : undefined;

  // Verify fix (major): filter changes can spawn overlapping requests (mount
  // fires before activeModel resolves, then again after). Without sequencing,
  // an older all-bundle response could land last and overwrite the newer
  // scoped one. Only the latest request may write state.
  const requestSeq = useRef(0);

  const refresh = useCallback(async () => {
    const seq = ++requestSeq.current;
    setLoading(true);
    const result = await apiClient.performance({
      mode: mode === 'all' ? undefined : mode,
      from: fromDay || undefined,
      to: toDay || undefined,
      bundle: bundleParam,
    });
    if (seq !== requestSeq.current) return; // superseded by a newer request
    setLoading(false);
    if (result.ok) {
      setReport(result.data);
      setError(null);
      setNoJournal(false);
      return;
    }
    if (result.status === 404 && result.error.includes('journal directory')) {
      setReport(null);
      setError(null);
      setNoJournal(true);
      return;
    }
    setError(result.error);
  }, [mode, fromDay, toDay, bundleParam]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const months = useMemo(() => (report ? monthsWithData(report.series) : []), [report]);
  const effectiveMonth = monthCursor ?? months[months.length - 1] ?? null;
  const calendar = useMemo(() => {
    if (!report || !effectiveMonth) return null;
    const { year, month } = parseMonthKey(effectiveMonth);
    return buildMonthCalendar(year, month, seriesByDay(report.series));
  }, [report, effectiveMonth]);

  const cards = useMemo(
    () =>
      report
        ? buildStatCards(report.headline, {
            showDollars,
            dollarsPerPoint: report.headline.point_value ?? DEFAULT_DOLLARS_PER_POINT,
          })
        : [],
    [report, showDollars],
  );

  const curve = useMemo(
    () => (report ? buildCumulativeCurve(report.series, 640, 220) : null),
    [report],
  );
  const bars = useMemo(() => (report ? buildDailyBars(report.series, 640, 220) : null), [report]);

  const hasRows = report !== null && (report.series.length > 0 || report.headline.predictions > 0);

  return (
    <div className="performance-page">
      <section className="panel perf-filters" aria-label="Performance filters">
        <div className="perf-filter-group">
          <span className="eyebrow">Mode</span>
          <div className="segmented-control" role="group" aria-label="Mode filter">
            {(['all', 'replay', 'live'] as Mode[]).map((value) => (
              <button
                key={value}
                className={mode === value ? 'active' : ''}
                onClick={() => setMode(value)}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
        <label className="perf-filter-group">
          <span className="eyebrow">From (trading day)</span>
          <input type="date" value={fromDay} onChange={(event) => setFromDay(event.target.value)} />
        </label>
        <label className="perf-filter-group">
          <span className="eyebrow">To (trading day)</span>
          <input type="date" value={toDay} onChange={(event) => setToDay(event.target.value)} />
        </label>
        {activeBundleId && (
          <div className="perf-filter-group">
            <span className="eyebrow">Bundle</span>
            <div className="segmented-control" role="group" aria-label="Bundle scope">
              <button
                className={bundleScope === 'active' ? 'active' : ''}
                onClick={() => setBundleScope('active')}
                title={activeBundleId}
              >
                active
              </button>
              <button
                className={bundleScope === 'all' ? 'active' : ''}
                onClick={() => setBundleScope('all')}
              >
                all
              </button>
            </div>
          </div>
        )}
        <div className="perf-filter-group perf-filter-actions">
          <button className="perf-refresh" onClick={() => void refresh()} disabled={loading}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
          <button
            className={`perf-toggle ${showDollars ? 'active' : ''}`}
            onClick={() => setShowDollars((current) => !current)}
            aria-pressed={showDollars}
          >
            $ view
          </button>
        </div>
      </section>

      {error && (
        <section className="panel perf-empty" role="status">
          <strong>Performance data unavailable</strong>
          <span>{error}</span>
        </section>
      )}

      {noJournal && (
        <section className="panel perf-empty" role="status">
          <strong>No journal yet</strong>
          <span>
            The prediction journal directory does not exist. Run a replay or live session with an
            active model; predictions, outcomes, and drops will be journaled automatically.
          </span>
        </section>
      )}

      {report && !hasRows && (
        <section className="panel perf-empty" role="status">
          <strong>No journal rows for these filters</strong>
          <span>
            {report.anomalies.lines_total === 0
              ? 'The journal directory exists but has no rows yet.'
              : 'Rows exist outside the current mode/date/bundle filters.'}
          </span>
        </section>
      )}

      {report && hasRows && (
        <>
          <section className="perf-cards" aria-label="Headline performance">
            {cards.map((card) => (
              <div key={card.key} className={`panel perf-card tone-${card.tone}`}>
                <span className="eyebrow">{card.label}</span>
                <strong>{card.value}</strong>
                <small>{card.detail}</small>
              </div>
            ))}
          </section>

          <section className="perf-charts">
            <div className="panel perf-chart-panel" aria-label="Cumulative net points">
              <div className="perf-section-head">
                <h3>Cumulative net points</h3>
                <span>
                  proxy pricing · {report.headline.priced_trades} priced /{' '}
                  {report.headline.resolved_trades} resolved
                </span>
              </div>
              {curve && curve.points.length > 0 ? (
                <svg
                  viewBox="0 0 640 220"
                  className="perf-svg"
                  role="img"
                  aria-label="Cumulative net points by trading day"
                >
                  {curve.zeroY !== null && (
                    <line x1="8" x2="632" y1={curve.zeroY} y2={curve.zeroY} className="perf-zero" />
                  )}
                  <path d={curve.path} className="perf-curve" />
                  {curve.points.map((point) => (
                    <circle key={point.day} cx={point.x} cy={point.y} r="4" className="perf-dot">
                      <title>{`${point.day}: ${formatPoints(point.cumulative)} pts cumulative`}</title>
                    </circle>
                  ))}
                </svg>
              ) : (
                <div className="perf-chart-empty">No priced trading days in range.</div>
              )}
            </div>
            <div className="panel perf-chart-panel" aria-label="Daily net points">
              <div className="perf-section-head">
                <h3>Daily net points</h3>
                <span>one bar per trading day (18:00 ET roll)</span>
              </div>
              {bars && bars.bars.length > 0 ? (
                <svg
                  viewBox="0 0 640 220"
                  className="perf-svg"
                  role="img"
                  aria-label="Daily net points by trading day"
                >
                  <line x1="8" x2="632" y1={bars.zeroY} y2={bars.zeroY} className="perf-zero" />
                  {bars.bars.map((bar) => (
                    <rect
                      key={bar.day}
                      x={bar.x}
                      y={bar.y}
                      width={bar.width}
                      height={Math.max(bar.height, 1)}
                      rx="2"
                      className={bar.positive ? 'perf-bar-pos' : 'perf-bar-neg'}
                    >
                      <title>{`${bar.day}: ${formatPoints(bar.net)} pts`}</title>
                    </rect>
                  ))}
                </svg>
              ) : (
                <div className="perf-chart-empty">No priced trading days in range.</div>
              )}
            </div>
          </section>

          {calendar && (
            <section className="panel perf-calendar-panel" aria-label="Trading-day calendar">
              <div className="perf-section-head">
                <h3>Trading-day calendar</h3>
                <div className="perf-month-nav">
                  <button
                    onClick={() => setMonthCursor(shiftMonth(effectiveMonth!, -1))}
                    aria-label="Previous month"
                  >
                    ‹
                  </button>
                  <strong>{calendar.label}</strong>
                  <button
                    onClick={() => setMonthCursor(shiftMonth(effectiveMonth!, 1))}
                    aria-label="Next month"
                  >
                    ›
                  </button>
                  <span>
                    month: {formatPoints(calendar.monthNet)} pts · {calendar.monthResolved} trades
                  </span>
                </div>
              </div>
              <div className="perf-calendar" role="table" aria-label={`Calendar ${calendar.label}`}>
                <div className="perf-cal-head" role="row">
                  {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Week'].map((label) => (
                    <span key={label} role="columnheader">
                      {label}
                    </span>
                  ))}
                </div>
                {calendar.weeks.map((week, weekIndex) => (
                  <div className="perf-cal-week" role="row" key={weekIndex}>
                    {week.cells.map((cell, cellIndex) =>
                      cell ? (
                        <div
                          key={cell.iso}
                          role="cell"
                          className="perf-cal-cell"
                          style={{ background: calendarCellColor(cell.intensity, cell.hasData) }}
                          title={
                            cell.hasData
                              ? `${cell.iso}: ${formatPoints(cell.net)} pts · ${cell.resolved} trades (${cell.priced} priced) · ${cell.predictions} predictions · ${cell.drops} drops`
                              : `${cell.iso}: no journal rows`
                          }
                        >
                          <span className="perf-cal-day">{cell.dayOfMonth}</span>
                          {cell.hasData && (
                            <>
                              <strong>{formatPoints(cell.net, 1)}</strong>
                              <small>{cell.resolved} trades</small>
                            </>
                          )}
                        </div>
                      ) : (
                        <div key={`empty-${weekIndex}-${cellIndex}`} role="cell" className="perf-cal-cell empty" />
                      ),
                    )}
                    <div role="cell" className={`perf-cal-cell week-total ${week.hasData ? '' : 'empty'}`}>
                      {week.hasData && (
                        <>
                          <strong>{formatPoints(week.weekNet)}</strong>
                          <small>{week.weekResolved} trades</small>
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>
              {calendar.offGrid.length > 0 && (
                <div className="perf-note" role="status">
                  Off-grid day keys (weekend-dated, should not occur on the 18:00 ET roll):{' '}
                  {calendar.offGrid.join(', ')}
                </div>
              )}
            </section>
          )}

          <section className="perf-tables">
            <div className="panel perf-table-panel" aria-label="Per-class breakdown">
              <h3>Per predicted class</h3>
              <table className="perf-table">
                <thead>
                  <tr>
                    <th>Class</th>
                    <th>Predicted</th>
                    <th>Precision</th>
                    <th>Recall</th>
                    <th>Win rate</th>
                    <th>Net pts</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(report.breakdowns.per_class).map(([name, row]) => (
                    <tr key={name}>
                      <td>{name}</td>
                      <td>{row.predicted}</td>
                      <td>{formatRatio(row.precision)}</td>
                      <td>{formatRatio(row.recall)}</td>
                      <td>{formatRatio(row.win_rate)}</td>
                      <td>{`${formatPoints(row.net_points.value)} (n=${row.net_points.n_priced})`}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <KeyedTable title="Per level kind" rows={report.breakdowns.per_level_kind} />
            <KeyedTable title="Per session" rows={report.breakdowns.per_session} />
            <div className="panel perf-table-panel" aria-label="Drops and funnel">
              <h3>Drops &amp; funnel</h3>
              <table className="perf-table">
                <tbody>
                  <tr>
                    <td>Predictions (cohort)</td>
                    <td>{report.funnel.predictions}</td>
                  </tr>
                  <tr>
                    <td>Eligible</td>
                    <td>{report.funnel.eligible}</td>
                  </tr>
                  <tr>
                    <td>Resolved</td>
                    <td>{report.funnel.resolved}</td>
                  </tr>
                  <tr>
                    <td>Dropped</td>
                    <td>{report.funnel.dropped}</td>
                  </tr>
                  <tr>
                    <td>Pending</td>
                    <td>{report.funnel.pending}</td>
                  </tr>
                  <tr>
                    <td>Priced</td>
                    <td>{report.funnel.priced}</td>
                  </tr>
                </tbody>
              </table>
              {Object.keys(report.breakdowns.drop_reasons).length > 0 && (
                <>
                  <h3>Drop reasons</h3>
                  <table className="perf-table">
                    <tbody>
                      {Object.entries(report.breakdowns.drop_reasons).map(([reason, count]) => (
                        <tr key={reason}>
                          <td>{reason}</td>
                          <td>{count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
              <div className="perf-note">
                Avg time to resolution:{' '}
                {formatDuration(report.headline.avg_time_to_resolution_seconds.value)} (n=
                {report.headline.avg_time_to_resolution_seconds.n}) · avg bars:{' '}
                {report.headline.avg_bars_to_resolution.value?.toFixed(1) ?? '—'}
              </div>
            </div>
          </section>

          {report.oos_comparison && (
            <section className="panel perf-oos-panel" aria-label="OOS vs live comparison">
              <div className="perf-section-head">
                <h3>OOS vs journal — {report.oos_comparison.bundle_id}</h3>
                <span>
                  pricing rules differ by side; each is labeled — they are not directly comparable
                </span>
              </div>
              <table className="perf-table perf-oos-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Bundle OOS</th>
                    <th>Journal (filtered)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Gated trades</td>
                    <td>{report.oos_comparison.oos.gated?.trade_count ?? '—'}</td>
                    <td>{report.oos_comparison.journal.eligible_trades}</td>
                  </tr>
                  <tr>
                    <td>Gated hit rate</td>
                    <td>
                      {report.oos_comparison.oos.gated?.precision !== null &&
                      report.oos_comparison.oos.gated !== null
                        ? `${((report.oos_comparison.oos.gated.precision ?? 0) * 100).toFixed(1)}%`
                        : '—'}
                    </td>
                    <td title={report.oos_comparison.journal.gated_hit_rate.definition}>
                      {formatRatio(report.oos_comparison.journal.gated_hit_rate)}
                    </td>
                  </tr>
                  <tr>
                    <td>Gated win rate</td>
                    <td>—</td>
                    <td>{formatRatio(report.oos_comparison.journal.gated_win_rate)}</td>
                  </tr>
                  <tr>
                    <td>Expectancy (pts)</td>
                    <td title={report.oos_comparison.oos.gated?.pricing_rule ?? ''}>
                      {report.oos_comparison.oos.gated?.expectancy_pts ?? '—'}
                    </td>
                    <td title={report.oos_comparison.journal.gated_expectancy_pts.pricing_rule}>
                      {report.oos_comparison.journal.gated_expectancy_pts.value === null
                        ? '—'
                        : `${report.oos_comparison.journal.gated_expectancy_pts.value.toFixed(2)} (n=${report.oos_comparison.journal.gated_expectancy_pts.n})`}
                    </td>
                  </tr>
                  <tr>
                    <td>Profit factor</td>
                    <td>{report.oos_comparison.oos.gated?.profit_factor ?? '—'}</td>
                    <td>
                      {report.headline.profit_factor.value === null
                        ? '—'
                        : report.headline.profit_factor.value.toFixed(2)}
                    </td>
                  </tr>
                  <tr>
                    <td>Class distribution (actual)</td>
                    <td>{formatDistribution(report.oos_comparison.oos.class_distribution?.actual)}</td>
                    <td>
                      {formatDistribution(report.oos_comparison.journal.class_distribution.actual)} (n=
                      {report.oos_comparison.journal.class_distribution.n})
                    </td>
                  </tr>
                  <tr>
                    <td>Class distribution (predicted)</td>
                    <td>
                      {formatDistribution(report.oos_comparison.oos.class_distribution?.predicted)}
                    </td>
                    <td>{formatDistribution(report.oos_comparison.journal.class_distribution.predicted)}</td>
                  </tr>
                  <tr>
                    <td>MFE / MAE (pts)</td>
                    <td>
                      <em>{report.oos_comparison.oos.mfe_mae_note}</em>
                    </td>
                    <td>
                      {formatDist(report.oos_comparison.journal.mfe_mae.mfe_pts)} /{' '}
                      {formatDist(report.oos_comparison.journal.mfe_mae.mae_pts)}
                    </td>
                  </tr>
                </tbody>
              </table>
              {report.oos_comparison.oos.quality_gates && (
                <div className="perf-gates">
                  <span className="eyebrow">
                    Bundle quality gates —{' '}
                    {report.oos_comparison.oos.quality_gates.all_passed ? 'all passed' : 'NOT all passed'}
                  </span>
                  <div className="perf-gate-chips">
                    {Object.entries(report.oos_comparison.oos.quality_gates.gates).map(
                      ([name, gate]) => (
                        <span
                          key={name}
                          className={`perf-gate-chip ${gate.passed ? 'pass' : 'fail'}`}
                          title={`value ${gate.value ?? '—'} vs threshold ${gate.threshold ?? '—'}`}
                        >
                          {gate.passed ? '✓' : '✗'} {name}
                        </span>
                      ),
                    )}
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="panel perf-quality" aria-label="Journal data quality">
            <span className="eyebrow">Journal data quality (whole directory, pre-filter)</span>
            <div className="perf-quality-row">
              <span>{report.anomalies.files_scanned} files</span>
              <span>{report.anomalies.lines_total} lines</span>
              <QualityCount label="unreadable files" value={report.anomalies.unreadable_files} />
              <QualityCount
                label="decode-error files"
                value={report.anomalies.decode_error_files}
              />
              <QualityCount label="malformed" value={report.anomalies.malformed_lines} />
              <QualityCount label="unknown type" value={report.anomalies.unknown_type_rows} />
              <QualityCount label="missing ids" value={report.anomalies.rows_missing_ids} />
              <QualityCount label="undated" value={report.anomalies.undated_rows} />
              <QualityCount
                label="dup predictions"
                value={report.anomalies.duplicate_prediction_rows}
              />
              <QualityCount label="dup outcomes" value={report.anomalies.duplicate_outcome_rows} />
              <QualityCount label="orphan outcomes" value={report.anomalies.orphan_outcomes} />
              <QualityCount label="orphan drops" value={report.anomalies.orphan_drops} />
              <QualityCount
                label="outcome+drop conflicts"
                value={report.anomalies.outcome_drop_conflicts}
              />
              <QualityCount
                label="unknown resolution"
                value={report.anomalies.scoped.unknown_resolution_trades}
              />
              <QualityCount label="unpriced" value={report.anomalies.scoped.unpriced_trades} />
            </div>
          </section>
        </>
      )}
    </div>
  );
}

function KeyedTable({ title, rows }: { title: string; rows: Record<string, PerformanceKeyRowDTO> }) {
  return (
    <div className="panel perf-table-panel" aria-label={title}>
      <h3>{title}</h3>
      <table className="perf-table">
        <thead>
          <tr>
            <th></th>
            <th>Predictions</th>
            <th>Resolved</th>
            <th>Win rate</th>
            <th>Net pts</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(rows).map(([name, row]) => (
            <tr key={name}>
              <td>{name}</td>
              <td>{row.predictions}</td>
              <td>{row.resolved}</td>
              <td>{formatRatio(row.win_rate)}</td>
              <td>{`${formatPoints(row.net_points)} (n=${row.priced})`}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QualityCount({ label, value }: { label: string; value: number }) {
  return <span className={value > 0 ? 'quality-flag' : ''}>{`${label}: ${value}`}</span>;
}

function formatDistribution(distribution: Record<string, number> | null | undefined): string {
  if (!distribution) return '—';
  return Object.entries(distribution)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, count]) => `${name}: ${count}`)
    .join(' · ');
}

function formatDist(stat: { mean: number | null; n: number }): string {
  if (stat.mean === null) return '—';
  return `${stat.mean.toFixed(1)} avg (n=${stat.n})`;
}
