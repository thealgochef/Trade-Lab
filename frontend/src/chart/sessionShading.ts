// COCKPIT P4b: session shading as a lightweight-charts v5 series primitive.
// Translucent full-height background bands per session, drawn behind the
// candles (drawBackground + zOrder bottom — the documented "time areas
// highlighting" pattern). Band geometry is computed by the pure bandRectsFor
// so the pixel math is unit-testable without a canvas.
import type { IChartApi, ISeriesPrimitive, SeriesAttachedParameter, Time, UTCTimestamp } from 'lightweight-charts';
import type { SessionBand, SessionBandName } from './viewModels';

export const SESSION_FILLS: Record<SessionBandName, string> = {
  asia: 'rgba(78, 161, 255, 0.05)',
  london: 'rgba(242, 184, 75, 0.05)',
  ny: 'rgba(54, 211, 153, 0.06)',
};

export type BandRect = { x: number; width: number; session: SessionBandName };

/**
 * Clip bands to the visible time range and convert to pane x-extents.
 * Band edges are always bar times (they exist on the scale); an edge that sits
 * off-screen clamps to the pane edge instead of relying on an off-scale lookup.
 */
export const bandRectsFor = (
  bands: SessionBand[],
  visible: { from: number; to: number } | null,
  timeToCoordinate: (time: UTCTimestamp) => number | null,
  paneWidth: number,
): BandRect[] => {
  if (visible === null || paneWidth <= 0) return [];
  const rects: BandRect[] = [];
  for (const band of bands) {
    const from = band.from as number;
    const to = band.to as number;
    if (to < visible.from || from > visible.to) continue;
    const x1 = from < visible.from ? 0 : timeToCoordinate(band.from) ?? 0;
    const x2 = to > visible.to ? paneWidth : timeToCoordinate(band.to) ?? paneWidth;
    if (x2 < x1) continue;
    rects.push({ x: x1, width: Math.max(1, x2 - x1), session: band.session });
  }
  return rects;
};

// Structural types for the canvas target (fancy-canvas is a transitive dep of
// lightweight-charts; declaring the used surface locally keeps our dependency
// graph explicit).
type BitmapScope = {
  context: CanvasRenderingContext2D;
  bitmapSize: { width: number; height: number };
  horizontalPixelRatio: number;
  verticalPixelRatio: number;
};

type RenderTarget = {
  useBitmapCoordinateSpace: (handler: (scope: BitmapScope) => void) => void;
};

export class SessionShadingPrimitive implements ISeriesPrimitive<Time> {
  private bands: SessionBand[] = [];
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;

  private readonly paneView = {
    zOrder: () => 'bottom' as const,
    renderer: () => ({
      draw: () => {},
      drawBackground: (target: unknown) => this.drawBands(target as RenderTarget),
    }),
  };

  attached(param: SeriesAttachedParameter<Time>): void {
    this.chart = param.chart;
    this.requestUpdate = param.requestUpdate;
  }

  detached(): void {
    this.chart = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.paneView];
  }

  setBands(bands: SessionBand[]): void {
    this.bands = bands;
    this.requestUpdate?.();
  }

  private drawBands(target: RenderTarget): void {
    const chart = this.chart;
    if (chart === null || this.bands.length === 0) return;
    const timeScale = chart.timeScale();
    const visible = timeScale.getVisibleRange() as { from: number; to: number } | null;
    const rects = bandRectsFor(
      this.bands,
      visible,
      (time) => timeScale.timeToCoordinate(time as Time),
      timeScale.width(),
    );
    if (rects.length === 0) return;
    target.useBitmapCoordinateSpace((scope) => {
      for (const rect of rects) {
        scope.context.fillStyle = SESSION_FILLS[rect.session];
        scope.context.fillRect(
          Math.round(rect.x * scope.horizontalPixelRatio),
          0,
          Math.max(1, Math.round(rect.width * scope.horizontalPixelRatio)),
          scope.bitmapSize.height,
        );
      }
    });
  }
}
