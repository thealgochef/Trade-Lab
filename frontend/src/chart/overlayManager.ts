import type { IPriceLine, ISeriesApi, SeriesMarker, Time } from 'lightweight-charts';
import { createSeriesMarkers } from 'lightweight-charts';
import { SessionShadingPrimitive } from './sessionShading';
import type { LevelOverlay, PositionLineOverlay, SessionBand } from './viewModels';

type MarkerApi = { setMarkers: (markers: SeriesMarker<Time>[]) => void; remove?: () => void };

// Enlarge the marker glyph (default is 1) so touch/prediction/outcome symbols read clearly
// against the candles instead of getting lost next to them.
const MARKER_SIZE = 2;

export class ChartOverlayManager {
  private readonly priceLines = new Map<string, IPriceLine>();
  // COCKPIT P4a: TP/SL barrier lines keyed by overlay id, own layer so level
  // syncs can never remove a position line and vice versa.
  private readonly positionLines = new Map<string, IPriceLine>();
  private readonly markerApi: MarkerApi;
  // COCKPIT P4b: translucent session bands behind the candles.
  private readonly sessionShading = new SessionShadingPrimitive();

  constructor(private readonly series: ISeriesApi<'Candlestick'>) {
    // Overlay layers are generic and data-driven so later phases can add ML or
    // execution annotations without parents touching chart internals.
    this.markerApi = createSeriesMarkers(series, []);
    series.attachPrimitive(this.sessionShading);
  }

  syncLevels(levels: LevelOverlay[]) {
    const nextIds = new Set(levels.map((level) => level.id));
    for (const [id, line] of this.priceLines) {
      if (!nextIds.has(id)) {
        this.series.removePriceLine(line);
        this.priceLines.delete(id);
      }
    }

    for (const level of levels) {
      const current = this.priceLines.get(level.id);
      if (current) this.series.removePriceLine(current);
      const line = this.series.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: level.lineWidth,
        lineStyle: level.lineStyle,
        title: level.title,
        axisLabelVisible: true,
      });
      this.priceLines.set(level.id, line);
    }
  }

  // TP/SL lines mirror syncLevels: ids that vanish (position closed / reset)
  // drop their lines; surviving ids are re-created at the latest price.
  syncPositionLines(lines: PositionLineOverlay[]) {
    const nextIds = new Set(lines.map((line) => line.id));
    for (const [id, line] of this.positionLines) {
      if (!nextIds.has(id)) {
        this.series.removePriceLine(line);
        this.positionLines.delete(id);
      }
    }

    for (const overlay of lines) {
      const current = this.positionLines.get(overlay.id);
      if (current) this.series.removePriceLine(current);
      const line = this.series.createPriceLine({
        price: overlay.price,
        color: overlay.color,
        lineWidth: overlay.lineWidth,
        lineStyle: overlay.lineStyle,
        title: overlay.title,
        axisLabelVisible: true,
      });
      this.positionLines.set(overlay.id, line);
    }
  }

  syncSessionBands(bands: SessionBand[]) {
    this.sessionShading.setBands(bands);
  }

  syncMarkers(markers: SeriesMarker<Time>[]) {
    // Render each marker's inline `text` as an always-on label (plus a larger glyph) so the
    // touch/prediction/outcome annotations are legible on the chart without hovering. The hover
    // tooltip still works for the full label; `id` is preserved for that hit-testing.
    this.markerApi.setMarkers(markers.map((marker) => ({ ...marker, size: MARKER_SIZE })));
  }

  destroy() {
    for (const line of this.priceLines.values()) this.series.removePriceLine(line);
    this.priceLines.clear();
    for (const line of this.positionLines.values()) this.series.removePriceLine(line);
    this.positionLines.clear();
    this.series.detachPrimitive(this.sessionShading);
    this.markerApi.remove?.();
  }
}
