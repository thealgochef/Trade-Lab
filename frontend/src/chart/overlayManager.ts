import type { IPriceLine, ISeriesApi, SeriesMarker, Time } from 'lightweight-charts';
import { createSeriesMarkers } from 'lightweight-charts';
import type { LevelOverlay } from './viewModels';

type MarkerApi = { setMarkers: (markers: SeriesMarker<Time>[]) => void; remove?: () => void };

// Enlarge the marker glyph (default is 1) so touch/prediction/outcome symbols read clearly
// against the candles instead of getting lost next to them.
const MARKER_SIZE = 2;

export class ChartOverlayManager {
  private readonly priceLines = new Map<string, IPriceLine>();
  private readonly markerApi: MarkerApi;

  constructor(private readonly series: ISeriesApi<'Candlestick'>) {
    // Overlay layers are generic and data-driven so later phases can add ML or
    // execution annotations without parents touching chart internals.
    this.markerApi = createSeriesMarkers(series, []);
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

  syncMarkers(markers: SeriesMarker<Time>[]) {
    // Render each marker's inline `text` as an always-on label (plus a larger glyph) so the
    // touch/prediction/outcome annotations are legible on the chart without hovering. The hover
    // tooltip still works for the full label; `id` is preserved for that hit-testing.
    this.markerApi.setMarkers(markers.map((marker) => ({ ...marker, size: MARKER_SIZE })));
  }

  destroy() {
    for (const line of this.priceLines.values()) this.series.removePriceLine(line);
    this.priceLines.clear();
    this.markerApi.remove?.();
  }
}
